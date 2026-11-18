#include "global.h"
#include "bmlib.h"
#include "native_msg.h"

#ifdef NATIVE_LOCALIZED_TEXT
#include "game_localization_declarations.h"
#include "localized_game_text_storage.h"
#endif

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
    struct InitialCacheRecord records[6];
};

volatile struct InitialCacheProbe gInitialCacheProbe;
extern int sActiveMsg;
extern void (*DecodeStringRAMFunc)(const char *, char *);

#ifdef NATIVE_SINGLETON_TEXT
const u8 gSingletonSentinel[] __attribute__((section(".singleton_sentinel"))) = { 1 };
const u32 gSingletonPoison __attribute__((section(".singleton_poison"))) = 0xFFFF0041;
#endif

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

static void RecordRawLookup(unsigned slot, int id, unsigned length)
{
    const char *text;
    unsigned index;
    volatile struct InitialCacheRecord *record;

    text = GetStringFromIndex(id);
    record = &gInitialCacheProbe.records[slot];
    record->id = id;
    record->length = length;
    for (index = 0; index < record->length; index++)
        record->bytes[index] = text[index];
}

void AgbMain(void)
{
    gInitialCacheProbe.phase = 1;
#ifdef NATIVE_LOCALIZED_TEXT
    gInitialCacheProbe.messageCount = GAME_LOCALIZATION_TARGET_COUNT;
#else
    gInitialCacheProbe.messageCount = MSG_COUNT;
#endif
    gInitialCacheProbe.bufferBytes = sizeof(struct MsgBuffer);
    gInitialCacheProbe.initialActiveId = sActiveMsg;
    StoreRoutinesToIRAM();
    gInitialCacheProbe.decoderAddress = (u32)DecodeStringRAMFunc;
#ifdef NATIVE_LOCALIZED_TEXT
    RecordRawLookup(0, 19, 11);
    RecordRawLookup(1, 20, 5);
    RecordRawLookup(2, 21, 5);
    RecordLookup(3, 22);
    RecordRawLookup(4, 26, 12);
    RecordLookup(5, 27);
#elif defined(NATIVE_CONTROL_TEXT)
    RecordRawLookup(0, MSG_CACHE_ZERO, 7);
    RecordRawLookup(1, MSG_CACHE_ONE, 6);
    RecordRawLookup(2, MSG_ODD, 5);
    RecordRawLookup(3, MSG_FACE_AFTER_ODD, 7);
    RecordRawLookup(4, MSG_COLOR_ZERO, 4);
    RecordRawLookup(5, MSG_COLOR_ARG_ZERO, 5);
#else
    RecordLookup(0, MSG_CACHE_ZERO);
    RecordLookup(1, MSG_CACHE_ZERO);
    RecordLookup(2, MSG_CACHE_ONE);
    RecordLookup(3, MSG_CACHE_ZERO);
    RecordLookup(4, MSG_ODD);
#ifdef NATIVE_SINGLETON_TEXT
    RecordLookup(5, MSG_FACE_AFTER_ODD);
#else
    RecordRawLookup(5, MSG_FACE_AFTER_ODD, 5);
#endif
#endif
    gInitialCacheProbe.phase = 2;
    for (;;)
        __asm__ volatile ("nop");
}
