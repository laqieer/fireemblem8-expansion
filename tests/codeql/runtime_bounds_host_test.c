#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmmap.h"
#include "bmmind.h"
#include "bmtrick.h"
#include "event.h"

struct Vec2 gBmMapSize;
u32 gEventSlots[EVENT_SLOT_COUNT];
u32 gEventSlotQueue[EVENT_SLOT_QUEUE_COUNT];

static int sFailures;

enum
{
    SCRIPT_BATTLE_HIT_COUNT = 7,
};

#define CHECK(condition) do { \
    if (!(condition)) { \
        printf("FAIL: %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        sFailures++; \
    } \
} while (0)

static void TestMapChangeBounds(void)
{
    struct MapChange change;

    memset(&change, 0, sizeof(change));
    gBmMapSize.x = 30;
    gBmMapSize.y = 20;

    change.xOrigin = 29;
    change.yOrigin = 19;
    change.xSize = 1;
    change.ySize = 1;
    CHECK(IsMapChangeInBounds(&change));

    change.xSize = 2;
    CHECK(!IsMapChangeInBounds(&change));

    change.xOrigin = 0xFF;
    change.xSize = 2;
    CHECK(!IsMapChangeInBounds(&change));

    change.xOrigin = 0;
    change.xSize = 1;
    change.yOrigin = 0xFF;
    change.ySize = 2;
    CHECK(!IsMapChangeInBounds(&change));
    CHECK(!IsMapChangeInBounds(NULL));
}

static void TestEventQueueBounds(void)
{
    int i;
    u32 value = 0xA5A5A5A5;

    memset(gEventSlots, 0, sizeof(gEventSlots));
    memset(gEventSlotQueue, 0, sizeof(gEventSlotQueue));

    CHECK(!SlotQueuePop(&value));
    CHECK(value == 0xA5A5A5A5);
    CHECK(gEventSlots[0xD] == 0);

    for (i = 0; i < EVENT_SLOT_QUEUE_COUNT; i++)
        CHECK(SlotQueuePush(0x100 + i));

    CHECK(gEventSlots[0xD] == EVENT_SLOT_QUEUE_COUNT);
    CHECK(!SlotQueuePush(0xDEADBEEF));
    CHECK(gEventSlots[0xD] == EVENT_SLOT_QUEUE_COUNT);
    CHECK(gEventSlotQueue[EVENT_SLOT_QUEUE_COUNT - 1] == 0x100 + EVENT_SLOT_QUEUE_COUNT - 1);

    CHECK(SlotQueuePop(&value));
    CHECK(value == 0x100);
    CHECK(gEventSlots[0xD] == EVENT_SLOT_QUEUE_COUNT - 1);
    CHECK(gEventSlotQueue[0] == 0x101);
}

static void TestScriptBattleSentinelCapacity(void)
{
    int i;
    int count;
    struct
    {
        struct BattleHit hits[SCRIPT_BATTLE_HIT_COUNT];
        u32 guard;
    } guarded;

    memset(&guarded, 0xA5, sizeof(guarded));
    memset(gEventSlotQueue, 0, sizeof(gEventSlotQueue));

    for (i = 0; i < SCRIPT_BATTLE_HIT_COUNT; i++)
    {
        ((u8 *)&gEventSlotQueue[i])[0] = 1;
        ((u8 *)&gEventSlotQueue[i])[1] = i + 1;
        ((u16 *)&gEventSlotQueue[i])[1] = 0x20 + i;
    }

    count = BuildScriptBattleHits(
        gEventSlotQueue,
        SCRIPT_BATTLE_HIT_COUNT,
        guarded.hits,
        ARRAY_COUNT(guarded.hits));
    CHECK(count == SCRIPT_BATTLE_HIT_COUNT - 1);

    for (i = 0; i < SCRIPT_BATTLE_HIT_COUNT - 1; i++)
        CHECK(!(guarded.hits[i].info & BATTLE_HIT_INFO_END));

    CHECK(guarded.hits[SCRIPT_BATTLE_HIT_COUNT - 1].info == BATTLE_HIT_INFO_END);
    CHECK(guarded.guard == 0xA5A5A5A5);
}

int main(void)
{
    TestMapChangeBounds();
    TestEventQueueBounds();
    TestScriptBattleSentinelCapacity();

    if (sFailures == 0)
    {
        puts("runtime_bounds_host_test: ok");
        return 0;
    }

    printf("%d failure(s)\n", sFailures);
    return 1;
}
