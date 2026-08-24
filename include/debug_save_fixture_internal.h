#ifndef GUARD_DEBUG_SAVE_FIXTURE_INTERNAL_H
#define GUARD_DEBUG_SAVE_FIXTURE_INTERNAL_H

#include "expansion_debug_save_fixture.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED && !defined(FE8_ARCHIVAL_BUILD)

struct DebugToolsUnitEditorState
{
    u32 targetState;
    s8 oldValues[DEBUGTOOLS_UNIT_EDIT_FIELD_COUNT];
    s8 previewValues[DEBUGTOOLS_UNIT_EDIT_FIELD_COUNT];
    u8 active;
    u8 closeExpected;
    u8 targetSlot;
    u8 targetCharacterNumber;
    u8 targetClassNumber;
    u8 targetX;
    u8 targetY;
    u8 previewField;
};

union DebugSaveFixtureStableStorage
{
    u8 retained[0x48];
    struct
    {
        struct DebugSaveFixturePreview preview;
        u8 openingPreview;
        u8 openingFinal;
        u8 handoff;
        u8 reserved;
    } fixture;
    struct DebugToolsUnitEditorState unitEditor;
};

extern union DebugSaveFixtureStableStorage sSaveStateStableLayout;

enum DebugSaveFixtureContinueResult DebugSaveFixture_ConsumePendingContinue(void);
void DebugSaveFixture_NotifyTitleScreenStarting(void);
int DebugSaveFixture_ShouldBlockSramWrite(const void *dest, u32 size);
int DebugSaveFixture_RecordBlockedWrite(enum DebugSaveFixtureWriteKind kind);
bool8 DebugSaveFixture_TryReadGlobalSaveInfo(struct GlobalSaveInfo *out);

void ReadGameSaveFromImage(int slot, const struct GameSaveBlock *src);
void ReadSuspendSaveFromImage(
    int resolvedSlot,
    const struct SuspendSaveBlock *src,
    const struct GameSaveBlock *backingGame);

#define DEBUG_SAVE_FIXTURE_WRITES_BLOCKED \
    (gDebugSaveFixtureProbe.phase == DEBUG_SAVE_FIXTURE_ACTIVE)

#else

#define DEBUG_SAVE_FIXTURE_WRITES_BLOCKED 0

static inline enum DebugSaveFixtureContinueResult
DebugSaveFixture_ConsumePendingContinue(void)
{
    return DEBUG_SAVE_FIXTURE_CONTINUE_NONE;
}

static inline void DebugSaveFixture_NotifyTitleScreenStarting(void)
{
}

static inline int DebugSaveFixture_ShouldBlockSramWrite(
    const void *dest,
    u32 size)
{
    (void)dest;
    (void)size;
    return FALSE;
}

static inline int DebugSaveFixture_RecordBlockedWrite(
    enum DebugSaveFixtureWriteKind kind)
{
    (void)kind;
    return FALSE;
}

static inline bool8 DebugSaveFixture_TryReadGlobalSaveInfo(
    struct GlobalSaveInfo *out)
{
    (void)out;
    return FALSE;
}

#endif

#endif
