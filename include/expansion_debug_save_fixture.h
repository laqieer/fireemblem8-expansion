#ifndef GUARD_EXPANSION_DEBUG_SAVE_FIXTURE_H
#define GUARD_EXPANSION_DEBUG_SAVE_FIXTURE_H

#include "global.h"
#include "bmsave.h"
#include "expansion_debugtools.h"

enum DebugSaveFixtureGameSlot
{
    DEBUG_SAVE_FIXTURE_GAME0 = SAVE_ID_GAME0,
    DEBUG_SAVE_FIXTURE_GAME1 = SAVE_ID_GAME1,
    DEBUG_SAVE_FIXTURE_GAME2 = SAVE_ID_GAME2,
    DEBUG_SAVE_FIXTURE_GAME_NONE = 0xFF
};

enum DebugSaveFixtureSuspendSlot
{
    DEBUG_SAVE_FIXTURE_SUSPEND_PRIMARY = SAVE_ID_SUSPEND,
    DEBUG_SAVE_FIXTURE_SUSPEND_ALTERNATE = SAVE_ID_SUSPEND_ALT,
    DEBUG_SAVE_FIXTURE_SUSPEND_NONE = 0xFF
};

enum DebugSaveFixtureSourceKind
{
    DEBUG_SAVE_FIXTURE_SOURCE_GAME,
    DEBUG_SAVE_FIXTURE_SOURCE_SUSPEND
};

enum DebugSaveFixtureTacticianMode
{
    DEBUG_SAVE_FIXTURE_TACTICIAN_KEEP,
    DEBUG_SAVE_FIXTURE_TACTICIAN_FIXED_MARKER
};

enum DebugSaveFixturePhase
{
    DEBUG_SAVE_FIXTURE_EMPTY,
    DEBUG_SAVE_FIXTURE_PREVIEW,
    DEBUG_SAVE_FIXTURE_ARMED,
    DEBUG_SAVE_FIXTURE_PENDING_CONTINUE,
    DEBUG_SAVE_FIXTURE_ACTIVE
};

enum DebugSaveFixtureResult
{
    DEBUG_SAVE_FIXTURE_OK,
    DEBUG_SAVE_FIXTURE_ERR_DISABLED,
    DEBUG_SAVE_FIXTURE_ERR_NOT_TITLE,
    DEBUG_SAVE_FIXTURE_ERR_BUSY,
    DEBUG_SAVE_FIXTURE_ERR_SOURCE_NOT_CURRENT,
    DEBUG_SAVE_FIXTURE_ERR_SOURCE_TARGET_INVALID,
    DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED,
    DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID,
    DEBUG_SAVE_FIXTURE_ERR_STALE_TARGET,
    DEBUG_SAVE_FIXTURE_ERR_CONFIRMATION_ORDER,
    DEBUG_SAVE_FIXTURE_ERR_PERSISTENCE_BLOCKED
};

enum DebugSaveFixtureAbortReason
{
    DEBUG_SAVE_FIXTURE_ABORT_CANCEL,
    DEBUG_SAVE_FIXTURE_ABORT_FORCED_TEARDOWN,
    DEBUG_SAVE_FIXTURE_ABORT_SOURCE_CHANGED,
    DEBUG_SAVE_FIXTURE_ABORT_CONSUME_FAILED,
    DEBUG_SAVE_FIXTURE_ABORT_TITLE_RETURN
};

enum DebugSaveFixtureContinueResult
{
    DEBUG_SAVE_FIXTURE_CONTINUE_NONE,
    DEBUG_SAVE_FIXTURE_CONTINUE_GAME,
    DEBUG_SAVE_FIXTURE_CONTINUE_SUSPEND,
    DEBUG_SAVE_FIXTURE_CONTINUE_FAILED
};

enum DebugSaveFixtureWriteKind
{
    DEBUG_SAVE_FIXTURE_WRITE_LOW_LEVEL,
    DEBUG_SAVE_FIXTURE_WRITE_GAME,
    DEBUG_SAVE_FIXTURE_WRITE_SUSPEND,
    DEBUG_SAVE_FIXTURE_WRITE_GLOBAL,
    DEBUG_SAVE_FIXTURE_WRITE_PREFS,
    DEBUG_SAVE_FIXTURE_WRITE_WIPE,
    DEBUG_SAVE_FIXTURE_WRITE_XMAP
};

struct DebugSaveFixtureOverrides
{
    u8 completionCount;
    u8 tacticianMode;
};

struct DebugSaveFixtureTarget
{
    u32 generation;
    u32 sourceHashLo;
    u32 sourceHashHi;
    u32 sourceBlockChecksum;
    u32 fixtureImageChecksum;
    u8 sourceKind;
    u8 sourceGameSlot;
    u8 resolvedSuspendSlot;
    u8 backingGameSlot;
};

struct DebugSaveFixtureProvenance
{
    u8 schemaVersion;
    u8 formatVersion;
    u16 compatEpoch;
    u8 sourceAbiId;
    char sourceConfigFingerprint[17];
    char sourceBuildCommitShort[9];
};

struct DebugSaveFixturePreview
{
    struct DebugSaveFixtureTarget target;
    struct DebugSaveFixtureProvenance provenance;
    enum SaveCompatState sourceCompatibility;
    enum SaveCompatState fixtureCompatibility;
    struct DebugSaveFixtureOverrides overrides;
};

struct DebugSaveFixtureProbe
{
    u32 phase;
    u32 lastResult;
    u32 generation;
    u32 prepareCount;
    u32 armCount;
    u32 continueRequestCount;
    u32 continueConsumeCount;
    u32 abortCount;
    u32 blockedWriteCount;
    u32 lastBlockedWriteKind;
    u32 sourceKind;
    u32 sourceSlot;
    u32 resolvedSuspendSlot;
    u32 backingGameSlot;
    u32 sourceHashLo;
    u32 sourceHashHi;
    u32 fixtureImageChecksum;
    u32 fixtureCompatibility;
    u32 fixtureCompletionCount;
    u32 liveCompletionCount;
    u32 imageMagic;
    u32 imageFormatEpoch;
};

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED && !defined(FE8_ARCHIVAL_BUILD)

enum DebugSaveFixtureResult DebugSaveFixture_PrepareGame(
    enum DebugSaveFixtureGameSlot slot,
    const struct DebugSaveFixtureOverrides *overrides,
    struct DebugSaveFixturePreview *preview);

enum DebugSaveFixtureResult DebugSaveFixture_PrepareLatestSuspend(
    const struct DebugSaveFixtureOverrides *overrides,
    struct DebugSaveFixturePreview *preview);

enum DebugSaveFixtureResult DebugSaveFixture_Arm(
    const struct DebugSaveFixtureTarget *target);

enum DebugSaveFixtureResult DebugSaveFixture_RequestContinue(
    const struct DebugSaveFixtureTarget *target);

void DebugSaveFixture_Abort(enum DebugSaveFixtureAbortReason reason);
int DebugSaveFixture_CanPrepare(void);
int DebugSaveFixture_IsContinuePending(void);
int DebugSaveFixture_IsActive(void);
int DebugSaveFixture_IsPersistenceBlocked(void);
enum DebugSaveFixturePhase DebugSaveFixture_GetPhase(void);
enum DebugSaveFixtureResult DebugSaveFixture_GetLastResult(void);
const struct DebugSaveFixturePreview *DebugSaveFixture_GetPreview(void);

extern struct DebugSaveFixtureProbe gDebugSaveFixtureProbe;

#else

static inline enum DebugSaveFixtureResult DebugSaveFixture_PrepareGame(
    enum DebugSaveFixtureGameSlot slot,
    const struct DebugSaveFixtureOverrides *overrides,
    struct DebugSaveFixturePreview *preview)
{
    (void)slot;
    (void)overrides;
    (void)preview;
    return DEBUG_SAVE_FIXTURE_ERR_DISABLED;
}

static inline enum DebugSaveFixtureResult DebugSaveFixture_PrepareLatestSuspend(
    const struct DebugSaveFixtureOverrides *overrides,
    struct DebugSaveFixturePreview *preview)
{
    (void)overrides;
    (void)preview;
    return DEBUG_SAVE_FIXTURE_ERR_DISABLED;
}

static inline enum DebugSaveFixtureResult DebugSaveFixture_Arm(
    const struct DebugSaveFixtureTarget *target)
{
    (void)target;
    return DEBUG_SAVE_FIXTURE_ERR_DISABLED;
}

static inline enum DebugSaveFixtureResult DebugSaveFixture_RequestContinue(
    const struct DebugSaveFixtureTarget *target)
{
    (void)target;
    return DEBUG_SAVE_FIXTURE_ERR_DISABLED;
}

static inline void DebugSaveFixture_Abort(enum DebugSaveFixtureAbortReason reason)
{
    (void)reason;
}

static inline int DebugSaveFixture_CanPrepare(void)
{
    return FALSE;
}

static inline int DebugSaveFixture_IsContinuePending(void)
{
    return FALSE;
}

static inline int DebugSaveFixture_IsActive(void)
{
    return FALSE;
}

static inline int DebugSaveFixture_IsPersistenceBlocked(void)
{
    return FALSE;
}

static inline enum DebugSaveFixturePhase DebugSaveFixture_GetPhase(void)
{
    return DEBUG_SAVE_FIXTURE_EMPTY;
}

static inline enum DebugSaveFixtureResult DebugSaveFixture_GetLastResult(void)
{
    return DEBUG_SAVE_FIXTURE_ERR_DISABLED;
}

static inline const struct DebugSaveFixturePreview *DebugSaveFixture_GetPreview(void)
{
    return NULL;
}

#endif

#endif
