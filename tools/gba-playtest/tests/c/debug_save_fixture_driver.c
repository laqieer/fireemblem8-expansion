#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "global.h"
#include "agb_sram.h"
#include "bmsave.h"
#include "save_format.h"
#include "expansion_debug_save_fixture.h"

#define CHECK(cond, message) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUG_SAVE_FIXTURE_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

extern u8 sGameStartSaveBuf[0x8000];
extern struct SaveBlocks *DebugSaveFixtureHost_GetSource(void);
extern void DebugSaveFixtureHost_SetCompatibility(enum SaveCompatState state);
extern void DebugSaveFixtureHost_SetTitleActive(int active);
extern int DebugSaveFixtureHost_GetGameLoadCount(void);
extern int DebugSaveFixtureHost_GetSuspendLoadCount(void);
extern int DebugSaveFixtureHost_GetLastLoadedSlot(void);

static void PopulateBlockInfo(struct SaveBlocks *image, int index)
{
    struct SaveBlockInfo *info = &image->saveBlockInfo[index];
    const void *payload;

    memset(info, 0, sizeof(*info));
    info->magic32 = SAVEMAGIC32;
    info->magic16 = SAVEMAGIC16;

    if (index <= SAVE_ID_GAME2)
    {
        payload = &image->gameSaveBlocks[index];
        info->kind = SAVEBLOCK_KIND_GAME;
        info->offset = offsetof(struct SaveBlocks, gameSaveBlocks)
            + index * sizeof(struct GameSaveBlock);
        info->size = sizeof(struct GameSaveBlock);
    }
    else
    {
        payload = &image->suspendSaveBlocks[index - SAVE_ID_SUSPEND];
        info->kind = SAVEBLOCK_KIND_SUSPEND;
        info->offset = offsetof(struct SaveBlocks, suspendSaveBlocks)
            + (index - SAVE_ID_SUSPEND) * sizeof(struct SuspendSaveBlock);
        info->size = sizeof(struct SuspendSaveBlock);
    }

    info->checksum32 = ComputeChecksum32(payload, info->size);
}

static void BuildCurrentSource(struct SaveBlocks *image)
{
    memset(image, 0xFF, sizeof(*image));
    memset(&image->globalSaveInfo, 0, sizeof(image->globalSaveInfo));
    memcpy(image->globalSaveInfo.name, "AGB-FE9", 8);
    image->globalSaveInfo.magic32 = SAVEMAGIC32;
    image->globalSaveInfo.magic16 = SAVEMAGIC16;
    image->globalSaveInfo.completed = 1;
    image->globalSaveInfo.cleared_playthroughs[0] = 9;
    image->globalSaveInfo.last_game_save_id = 0;
    image->globalSaveInfo.last_suspend_slot = 1;
    image->globalSaveInfo.checksum = Checksum16(
        &image->globalSaveInfo,
        GLOBALSIZEINFO_SIZE_FOR_CHECKSUM);

    BuildCurrentExpansionSaveMeta(&image->expansionSaveMeta);

    memset(&image->gameSaveBlocks[0], 0, sizeof(struct GameSaveBlock));
    image->gameSaveBlocks[0].playSt.gameSaveSlot = 0;
    memcpy(image->gameSaveBlocks[0].playSt.playerName, "USER", 5);
    image->gameSaveBlocks[0].bonusClaimFlags = 0x13579BDF;
    PopulateBlockInfo(image, SAVE_ID_GAME0);

    memset(
        &image->suspendSaveBlocks[1],
        0,
        sizeof(struct SuspendSaveBlock));
    image->suspendSaveBlocks[1].playSt.gameSaveSlot = 0;
    image->suspendSaveBlocks[1].playSt.chapterIndex = 2;
    image->suspendSaveBlocks[1].playSt.xCursor = 9;
    image->suspendSaveBlocks[1].playSt.yCursor = 4;
    memcpy(image->suspendSaveBlocks[1].playSt.playerName, "USER", 5);
    PopulateBlockInfo(image, SAVE_ID_SUSPEND_ALT);
}

static int ImageIsZero(void)
{
    int i;

    for (i = 0; i < 0x8000; ++i)
        if (sGameStartSaveBuf[i] != 0)
            return FALSE;

    return TRUE;
}

int main(void)
{
    struct SaveBlocks *source = DebugSaveFixtureHost_GetSource();
    static u8 baseline[0x8000];
    struct SaveBlocks *fixture = (struct SaveBlocks *)sGameStartSaveBuf;
    struct DebugSaveFixtureOverrides overrides;
    struct DebugSaveFixturePreview preview;
    struct GlobalSaveInfo fixtureGlobal;
    enum DebugSaveFixtureContinueResult continueResult;
    enum SaveCompatState incompatibleStates[] = {
        SAVE_COMPAT_EMPTY,
        SAVE_COMPAT_VALID_LEGACY_OR_VANILLA,
        SAVE_COMPAT_HEADER_CORRUPT,
        SAVE_COMPAT_METADATA_CORRUPT,
        SAVE_COMPAT_MIGRATABLE_OLDER,
        SAVE_COMPAT_NEWER_UNSUPPORTED,
        SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE
    };
    int i;

    BuildCurrentSource(source);
    memcpy(baseline, source, sizeof(baseline));
    overrides.completionCount = 3;
    overrides.tacticianMode =
        DEBUG_SAVE_FIXTURE_TACTICIAN_FIXED_MARKER;

    CHECK(DebugSaveFixture_CanPrepare(), "title context must allow preparation");
    CHECK(
        DebugSaveFixture_PrepareLatestSuspend(&overrides, &preview)
            == DEBUG_SAVE_FIXTURE_OK,
        "valid latest suspend must prepare");
    CHECK(
        memcmp(source, baseline, sizeof(baseline)) == 0,
        "preparation must leave all source SRAM bytes unchanged");
    CHECK(
        preview.target.resolvedSuspendSlot == SAVE_ID_SUSPEND_ALT
            && preview.target.backingGameSlot == SAVE_ID_GAME0,
        "latest suspend target identity must pin alternate suspend and game 0");
    CHECK(
        preview.sourceCompatibility == SAVE_COMPAT_CURRENT
            && preview.fixtureCompatibility == SAVE_COMPAT_CURRENT,
        "source and fixture must both classify CURRENT");
    CHECK(
        memcmp(&fixture->expansionSaveMeta.magic, "FSAV", 4) == 0
            && fixture->expansionSaveMeta.formatVersion == 2
            && fixture->expansionSaveMeta.compatEpoch == 2,
        "fixture metadata bytes must be FSAV format 2 epoch 2");
    CHECK(
        fixture->globalSaveInfo.completed == 1
            && memcmp(
                fixture->globalSaveInfo.cleared_playthroughs,
                "\x01\x02\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00",
                12) == 0,
        "fixture completion bytes must be exactly 1,2,3,0..0");
    CHECK(
        memcmp(
            fixture->suspendSaveBlocks[1].playSt.playerName,
            "FIXTURE\0\0\0\0",
            11) == 0,
        "fixture suspend tactician bytes must be FIXTURE plus NUL padding");
    CHECK(
        memcmp(
            fixture->gameSaveBlocks[0].playSt.playerName,
            "FIXTURE\0\0\0\0",
            11) == 0,
        "fixture backing game tactician bytes must match");
    CHECK(
        fixture->saveBlockInfo[SAVE_ID_SUSPEND_ALT].offset
                == offsetof(struct SaveBlocks, suspendSaveBlocks[1])
            && fixture->saveBlockInfo[SAVE_ID_SUSPEND_ALT].size
                == sizeof(struct SuspendSaveBlock),
        "alternate suspend block info must use canonical offset and size");

    DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CANCEL);
    CHECK(ImageIsZero(), "cancel must zeroize the complete volatile image");
    CHECK(
        memcmp(source, baseline, sizeof(baseline)) == 0,
        "cancel must leave all source bytes unchanged");

    CHECK(
        DebugSaveFixture_PrepareLatestSuspend(&overrides, &preview)
            == DEBUG_SAVE_FIXTURE_OK,
        "stale-source setup must prepare");
    source->reserved[0] ^= 1;
    CHECK(
        DebugSaveFixture_Arm(&preview.target)
            == DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED,
        "a one-byte source change must invalidate Arm");
    CHECK(ImageIsZero(), "stale-source abort must zeroize the image");
    memcpy(source, baseline, sizeof(baseline));

    CHECK(
        DebugSaveFixture_PrepareLatestSuspend(&overrides, &preview)
            == DEBUG_SAVE_FIXTURE_OK,
        "continue setup must prepare");
    CHECK(
        DebugSaveFixture_Arm(&preview.target) == DEBUG_SAVE_FIXTURE_OK,
        "first confirmation must arm");
    CHECK(
        DebugSaveFixture_RequestContinue(&preview.target)
            == DEBUG_SAVE_FIXTURE_OK,
        "second confirmation must queue one continue");
    CHECK(DebugSaveFixture_IsContinuePending(), "continue must be pending");

    continueResult = DebugSaveFixture_ConsumePendingContinue();
    CHECK(
        continueResult == DEBUG_SAVE_FIXTURE_CONTINUE_SUSPEND,
        "game-control consume must select suspend route");
    CHECK(
        DebugSaveFixtureHost_GetSuspendLoadCount() == 1
            && DebugSaveFixtureHost_GetGameLoadCount() == 0
            && DebugSaveFixtureHost_GetLastLoadedSlot()
                == SAVE_ID_SUSPEND_ALT,
        "consume must call the source-neutral suspend loader exactly once");
    CHECK(
        memcmp(gPlaySt.playerName, "FIXTURE\0\0\0\0", 11) == 0,
        "live tactician state must come from the volatile fixture");
    CHECK(ImageIsZero(), "consume must zeroize the full volatile image");
    CHECK(DebugSaveFixture_IsActive(), "consume must activate the sandbox");
    CHECK(
        DebugSaveFixture_TryReadGlobalSaveInfo(&fixtureGlobal)
            && fixtureGlobal.completed == 1
            && fixtureGlobal.cleared_playthroughs[0] == 1
            && fixtureGlobal.cleared_playthroughs[1] == 2
            && fixtureGlobal.cleared_playthroughs[2] == 3,
        "active global reads must use the fixture cache");
    CHECK(
        DebugSaveFixture_ShouldBlockSramWrite(
            (const void *)CART_SRAM_ADDR,
            4),
        "active sandbox must block a cartridge-range write");
    CHECK(
        !DebugSaveFixture_ShouldBlockSramWrite((const void *)0x02000000, 4),
        "active sandbox must not block an EWRAM write");
    CHECK(
        gDebugSaveFixtureProbe.blockedWriteCount == 1
            && gDebugSaveFixtureProbe.lastBlockedWriteKind
                == DEBUG_SAVE_FIXTURE_WRITE_LOW_LEVEL,
        "blocked write must be visible in the probe");
    CHECK(
        memcmp(source, baseline, sizeof(baseline)) == 0,
        "active continue and blocked writes must preserve every source byte");

    DebugSaveFixture_NotifyTitleScreenStarting();
    CHECK(
        DebugSaveFixture_GetPhase() == DEBUG_SAVE_FIXTURE_EMPTY
            && !DebugSaveFixture_IsPersistenceBlocked(),
        "title return must clear sandbox ownership and the write guard");

    CHECK(
        DebugSaveFixture_PrepareLatestSuspend(&overrides, &preview)
            == DEBUG_SAVE_FIXTURE_OK,
        "confirmation-order setup must prepare");
    CHECK(
        DebugSaveFixture_RequestContinue(&preview.target)
            == DEBUG_SAVE_FIXTURE_ERR_CONFIRMATION_ORDER,
        "continue before Arm must fail closed");
    DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CANCEL);

    CHECK(
        DebugSaveFixture_PrepareLatestSuspend(&overrides, &preview)
            == DEBUG_SAVE_FIXTURE_OK
            && DebugSaveFixture_Arm(&preview.target)
                == DEBUG_SAVE_FIXTURE_OK,
        "post-Arm stale-source setup must arm");
    source->reserved[1] ^= 1;
    CHECK(
        DebugSaveFixture_RequestContinue(&preview.target)
            == DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED,
        "a source change after Arm must invalidate final confirmation");
    CHECK(ImageIsZero(), "post-Arm stale source must zeroize the image");
    memcpy(source, baseline, sizeof(baseline));

    CHECK(
        DebugSaveFixture_PrepareLatestSuspend(&overrides, &preview)
            == DEBUG_SAVE_FIXTURE_OK
            && DebugSaveFixture_Arm(&preview.target)
                == DEBUG_SAVE_FIXTURE_OK
            && DebugSaveFixture_RequestContinue(&preview.target)
                == DEBUG_SAVE_FIXTURE_OK,
        "consume-failure setup must queue");
    fixture->expansionSaveMeta.magic[0] ^= 1;
    CHECK(
        DebugSaveFixture_ConsumePendingContinue()
            == DEBUG_SAVE_FIXTURE_CONTINUE_FAILED,
        "consume-time image corruption must fail before map start");
    CHECK(ImageIsZero(), "consume-time failure must zeroize the image");
    CHECK(
        memcmp(source, baseline, sizeof(baseline)) == 0,
        "consume-time failure must preserve every source byte");

    CHECK(
        DebugSaveFixture_PrepareGame(
            DEBUG_SAVE_FIXTURE_GAME0,
            &overrides,
            &preview)
            == DEBUG_SAVE_FIXTURE_OK
            && DebugSaveFixture_Arm(&preview.target)
                == DEBUG_SAVE_FIXTURE_OK
            && DebugSaveFixture_RequestContinue(&preview.target)
                == DEBUG_SAVE_FIXTURE_OK,
        "valid game fixture must pass both confirmations");
    CHECK(
        DebugSaveFixture_ConsumePendingContinue()
            == DEBUG_SAVE_FIXTURE_CONTINUE_GAME,
        "game target must select the game-control game route");
    CHECK(
        DebugSaveFixtureHost_GetGameLoadCount() == 1
            && DebugSaveFixtureHost_GetSuspendLoadCount() == 1
            && DebugSaveFixtureHost_GetLastLoadedSlot() == SAVE_ID_GAME0,
        "game target must call the source-neutral game loader once");
    DebugSaveFixture_NotifyTitleScreenStarting();

    for (i = 0; i < (int)(sizeof(incompatibleStates) / sizeof(incompatibleStates[0])); ++i)
    {
        DebugSaveFixtureHost_SetCompatibility(incompatibleStates[i]);
        CHECK(
            DebugSaveFixture_PrepareGame(
                DEBUG_SAVE_FIXTURE_GAME0,
                &overrides,
                &preview)
                == DEBUG_SAVE_FIXTURE_ERR_SOURCE_NOT_CURRENT,
            "every non-CURRENT source state must fail closed");
        CHECK(
            memcmp(source, baseline, sizeof(baseline)) == 0,
            "non-CURRENT rejection must preserve every source byte");
    }

    DebugSaveFixtureHost_SetCompatibility(SAVE_COMPAT_CURRENT);
    source->saveBlockInfo[SAVE_ID_GAME0].checksum32 ^= 1;
    CHECK(
        DebugSaveFixture_PrepareGame(
            DEBUG_SAVE_FIXTURE_GAME0,
            &overrides,
            &preview)
            == DEBUG_SAVE_FIXTURE_ERR_SOURCE_TARGET_INVALID,
        "CURRENT with an invalid selected block must fail closed");
    memcpy(source, baseline, sizeof(baseline));

    DebugSaveFixtureHost_SetTitleActive(FALSE);
    CHECK(
        DebugSaveFixture_PrepareGame(
            DEBUG_SAVE_FIXTURE_GAME0,
            &overrides,
            &preview)
            == DEBUG_SAVE_FIXTURE_ERR_NOT_TITLE,
        "map/prep-style non-title context must reject preparation");
    CHECK(
        memcmp(source, baseline, sizeof(baseline)) == 0,
        "non-title rejection must preserve every source byte");

    printf("DEBUG_SAVE_FIXTURE_HOST_TEST: PASS\n");
    return 0;
}
