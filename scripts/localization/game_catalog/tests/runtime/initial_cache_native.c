#include "global.h"
#include "bmlib.h"
#include "native_msg.h"

struct InitialCacheRecord
{
    u32 id;
    u32 length;
    char bytes[16];
};

struct InitialCacheProbe
{
    u32 phase;
    u32 messageCount;
    u32 bufferBytes;
    u32 decoderAddress;
    u32 initialActiveId;
    struct InitialCacheRecord records[4];
};

volatile struct InitialCacheProbe gInitialCacheProbe;
extern int sActiveMsg;
extern void (*DecodeStringRAMFunc)(const char *, char *);

static void RecordLookup(unsigned slot, int id)
{
    const char *text;
    unsigned index;
    volatile struct InitialCacheRecord *record;

    text = GetStringFromIndex(id);
    record = &gInitialCacheProbe.records[slot];
    record->id = id;
    for (index = 0; index + 1 < sizeof(record->bytes) && text[index] != 0; index++)
        record->bytes[index] = text[index];
    record->bytes[index] = 0;
    record->length = index;
}

void AgbMain(void)
{
    gInitialCacheProbe.phase = 1;
    gInitialCacheProbe.messageCount = MSG_COUNT;
    gInitialCacheProbe.bufferBytes = sizeof(struct MsgBuffer);
    gInitialCacheProbe.initialActiveId = sActiveMsg;
    StoreRoutinesToIRAM();
    gInitialCacheProbe.decoderAddress = (u32)DecodeStringRAMFunc;
    RecordLookup(0, MSG_CACHE_ZERO);
    RecordLookup(1, MSG_CACHE_ZERO);
    RecordLookup(2, MSG_CACHE_ONE);
    RecordLookup(3, MSG_CACHE_ZERO);
    gInitialCacheProbe.phase = 2;
    for (;;)
        __asm__ volatile ("nop");
}
