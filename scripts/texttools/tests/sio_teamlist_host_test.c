#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmsave.h"
#include "bmunit.h"
#include "constants/msg.h"
#include "sio.h"

struct LinkArenaTeamEnt
{
    char name[MULTIARENA_TEAMNAME_SIZE + 1];
    u8 unk_0f;
    u8 unk_10;
    u8 padding[3];
};

static int sFailures;
static const char *sLocalizedEmptyName;
static u32 sLastCapacity;
static int sResolveCalls;
static struct Unit sUnits[MULTIARENA_MAX_TEAMS * MULTIARENA_UNITS_PER_TEAM + 1];

struct LinkArenaTeamEnt gLinkArenaTeamList[MULTIARENA_MAX_TEAMS];

#define CHECK(condition) do { \
    if (!(condition)) { \
        printf("FAIL: %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        sFailures++; \
    } \
} while (0)

void InitUnits(void)
{
}

bool ReadMultiArenaSaveTeamName(int team, char *dst)
{
    (void)team;
    (void)dst;
    return FALSE;
}

bool ReadMultiArenaSaveTeam(int team, struct Unit *units, char *name)
{
    (void)team;
    (void)units;
    name[0] = '\0';
    return FALSE;
}

struct Unit *GetUnit(int id)
{
    return &sUnits[id];
}

char *GetStringFromIndexInBufferWithLimit(
    int index,
    char *buffer,
    u32 capacity)
{
    size_t length;

    CHECK(index == MSG_0CC);
    sLastCapacity = capacity;
    sResolveCalls++;
    length = strlen(sLocalizedEmptyName);
    if (length >= capacity)
        length = capacity - 1;
    memcpy(buffer, sLocalizedEmptyName, length);
    buffer[length] = '\0';
    return buffer;
}

static void ResetHarness(const char *localizedEmptyName)
{
    memset(gLinkArenaTeamList, 0xA5, sizeof(gLinkArenaTeamList));
    sLocalizedEmptyName = localizedEmptyName;
    sLastCapacity = 0;
    sResolveCalls = 0;
}

static void CheckFieldGuards(void)
{
    int i;

    for (i = 0; i < MULTIARENA_MAX_TEAMS; i++)
    {
        CHECK(
            memchr(
                gLinkArenaTeamList[i].name,
                '\0',
                sizeof(gLinkArenaTeamList[i].name)) != NULL);
        CHECK(gLinkArenaTeamList[i].unk_0f == (i | 0x80));
        CHECK(gLinkArenaTeamList[i].padding[0] == 0xA5);
        CHECK(gLinkArenaTeamList[i].padding[1] == 0xA5);
        CHECK(gLinkArenaTeamList[i].padding[2] == 0xA5);
    }
}

static void TestJapanesePayloadFitsActualField(void)
{
    int i;
    static const char japaneseEmptyName[] = "なし";

    CHECK(strlen(japaneseEmptyName) <= MULTIARENA_TEAMNAME_SIZE);
    ResetHarness(japaneseEmptyName);
    CHECK(LoadLinkArenaTeamList(0, 0) == MULTIARENA_MAX_TEAMS);
    CHECK(sResolveCalls == MULTIARENA_MAX_TEAMS);
    CHECK(sLastCapacity == sizeof(gLinkArenaTeamList[0].name));
    for (i = 0; i < MULTIARENA_MAX_TEAMS; i++)
        CHECK(strcmp(gLinkArenaTeamList[i].name, japaneseEmptyName) == 0);
    CheckFieldGuards();
}

static void TestOversizePayloadCannotCrossActualField(void)
{
    int i;

    ResetHarness("123456789012345678901234");
    CHECK(LoadLinkArenaTeamList(0, 0) == MULTIARENA_MAX_TEAMS);
    CHECK(sResolveCalls == MULTIARENA_MAX_TEAMS);
    CHECK(sLastCapacity == sizeof(gLinkArenaTeamList[0].name));
    for (i = 0; i < MULTIARENA_MAX_TEAMS; i++)
        CHECK(strlen(gLinkArenaTeamList[i].name) == MULTIARENA_TEAMNAME_SIZE);
    CheckFieldGuards();
}

int main(void)
{
    TestJapanesePayloadFitsActualField();
    TestOversizePayloadCannotCrossActualField();

    if (sFailures == 0)
    {
        puts("sio_teamlist_host_test: ok");
        return 0;
    }

    printf("%d failure(s)\n", sFailures);
    return 1;
}
