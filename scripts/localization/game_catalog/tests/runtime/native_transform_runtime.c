#include "global.h"
#include "bmlib.h"
#include "bmmind.h"
#include "bmitem.h"
#include "bmunit.h"
#include "native_msg.h"

struct NativeTransformRecord
{
    u32 id;
    u32 length;
    u8 bytes[32];
};

struct InitialCacheProbe
{
    u32 phase;
    u32 messageCount;
    u32 bufferBytes;
    u32 decoderAddress;
    u32 initialActiveId;
    struct NativeTransformRecord records[39];
};

volatile struct InitialCacheProbe gInitialCacheProbe;
struct ActionData gActionData;
struct ItemData gItemData[1] = { { .nameTextId = MSG_ITEM } };
extern struct MsgBuffer sMsgString;
extern int sActiveMsg;
extern void (*DecodeStringRAMFunc)(const char *, char *);
#ifdef NATIVE_TRANSFORM_CAPACITY
extern volatile u32 gNativeTransformGuard;
#endif

const struct CharacterData *GetCharacterData(int id)
{
    static const struct CharacterData characters[] = {
        { .nameTextId = MSG_CHARACTER },
        { .nameTextId = MSG_CHARACTER_1 },
        { .nameTextId = MSG_CHARACTER_2 },
        { .nameTextId = MSG_CHARACTER_3 },
    };
    return &characters[id];
}

static void Record(unsigned slot, const void *text, unsigned length)
{
    const u8 *bytes = text;
    volatile struct NativeTransformRecord *record = &gInitialCacheProbe.records[slot];
    unsigned index;

    record->id = slot;
    record->length = length;
    for (index = 0; index < length; index++)
        record->bytes[index] = bytes[index];
}

static void SetInput(const u8 *input, unsigned length)
{
    unsigned index;
    for (index = 0; index < length; index++)
        sMsgString.buffer1[index] = input[index];
}

static void SetName(char first, char second, char third)
{
    gPlaySt.playerName[0] = first;
    gPlaySt.playerName[1] = second;
    gPlaySt.playerName[2] = third;
    gPlaySt.playerName[3] = 0;
}

void AgbMain(void)
{
    static const u8 color[] = {0x80, 1, 0, 'B', 0x80, 0x20, 0};
    static const u8 glyphs[] = {
        0x93, 0x94, 0xE9, 0x81, 0x40, 0x10, 0, 1, 'B', 0x80, 0x20, 0
    };
    static const u8 highGlyph[] = {0xC2, 0x80, 0x20, 0};
    static const u8 embeddedControls[] = {
        'A', 0x10, 0x80, 0x20, 'B', 0x80, 1, 0x80, 'C', 0x80, 0x20, 0
    };
    u8 faceTerminator[] = {'A', 0x10, 1, 0x1F, 0};
    u8 colorTerminator[] = {'A', 0x80, 1, 0x1F, 0};
    u8 winkTerminator[] = {'A', 0x80, 0x1F, 0x1F, 0};
    u8 loneTerminator[] = {0x80, 0x1F, 0};
    u8 legacyTerminator[] = {0xE9, 0x93, 0x80, 1, 0, 'Z', 0x1F, 0};
    char prefixed[16] = {'A', 0x10, 0x10, 0, 'B', 0};
    char legacyPrefixed[16] = {0xE9, 0x93, 0x80, 1, 0, 'Z', 0};
    const char colorPrefix[] = {'P', 0x80, 1, 0, 'Q', 0};
    const char facePrefix[] = {'P', 0x10, 0x10, 0, 'Q', 0};
    char colorPrefixTarget[16] = {'S', 0};
    char facePrefixTarget[16] = {'S', 0};
    u8 sample[9];
    const char *result;
    unsigned index;
#ifdef NATIVE_TRANSFORM_CAPACITY
    u32 guard;
#endif

    gInitialCacheProbe.phase = 1;
    gInitialCacheProbe.messageCount = MSG_COUNT;
    gInitialCacheProbe.bufferBytes = sizeof(struct MsgBuffer);
    gInitialCacheProbe.initialActiveId = sActiveMsg;
    gPlaySt.unk1C[0] = 0;
    gPlaySt.unk1C[1] = 1;
    gPlaySt.unk1E = 2;
    gPlaySt.unk1F = 3;
    StoreRoutinesToIRAM();
    gInitialCacheProbe.decoderAddress = (u32)DecodeStringRAMFunc;
    SetName('T', 0, 0);

    result = GetStringFromIndex(MSG_CACHE_ZERO);
    Record(0, result, 8);
    Record(1, StrInsertTact(), 7);
    Record(2, StringInsertSpecialPrefixByCtrl(), 7);
    InsertPrefix(prefixed, "P", FALSE);
    Record(3, prefixed, 7);

    GetStringFromIndex(MSG_SPECIAL);
    Record(4, StringInsertSpecialPrefixByCtrl(), 19);

    SetInput(color, sizeof(color));
    Record(5, StrInsertTact(), 6);
    Record(6, StringInsertSpecialPrefixByCtrl(), 6);

    SetMsgTerminator((signed char *)faceTerminator);
    Record(7, faceTerminator, sizeof(faceTerminator));
    SetMsgTerminator((signed char *)colorTerminator);
    Record(8, colorTerminator, sizeof(colorTerminator));
    SetMsgTerminator((signed char *)winkTerminator);
    Record(9, winkTerminator, 4);
    SetMsgTerminator((signed char *)&loneTerminator[1]);
    Record(10, loneTerminator, sizeof(loneTerminator));

    SetInput(glyphs, sizeof(glyphs));
    Record(11, StrInsertTact(), 11);
    SetInput(highGlyph, sizeof(highGlyph));
    Record(12, StrInsertTact(), 3);
    GetStringFromIndex(MSG_CACHE_ONE);
    Record(13, StrInsertTact(), 6);

    SetName('T', 0, 0);
    gItemData[0].nameTextId = MSG_CACHE_ZERO;
    GetStringFromIndex(MSG_SPECIAL);
    Record(21, StringInsertSpecialPrefixByCtrl(), 20);

    SetName('T', 0, 0);
    gItemData[0].nameTextId = MSG_ITEM;
    result = GetStringFromIndex(MSG_AMBIGUOUS);
    Record(24, result, 19);
    Record(25, StrInsertTact(), 17);
    Record(26, StringInsertSpecialPrefixByCtrl(), 23);
    InsertPrefix(legacyPrefixed, "P", FALSE);
    Record(27, legacyPrefixed, 8);
    SetMsgTerminator((signed char *)legacyTerminator);
    Record(28, legacyTerminator, sizeof(legacyTerminator));
    GetStringFromIndex(MSG_ALL_CHARACTERS);
    Record(29, StringInsertSpecialPrefixByCtrl(), 23);
    SetInput(embeddedControls, sizeof(embeddedControls));
    Record(30, StrInsertTact(), 11);
    Record(31, StringInsertSpecialPrefixByCtrl(), 11);
    InsertPrefix(colorPrefixTarget, colorPrefix, FALSE);
    Record(34, colorPrefixTarget, 7);
    InsertPrefix(facePrefixTarget, facePrefix, FALSE);
    Record(35, facePrefixTarget, 7);

#ifdef NATIVE_TRANSFORM_CAPACITY
    gNativeTransformGuard = 0x5A5A5A5A;
    for (index = 0; index < 250; index++)
        sMsgString.buffer5[index] = 'a';
    sMsgString.buffer5[250] = 0;
    InsertPrefix((char *)sMsgString.buffer5, colorPrefix, FALSE);
    for (index = 0; index < 5; index++)
        sample[index] = sMsgString.buffer5[index];
    for (index = 0; index < 4; index++)
        sample[index + 5] = sMsgString.buffer5[252 + index];
    Record(36, sample, 9);

    for (index = 0; index < 251; index++)
        sMsgString.buffer5[index] = 'a';
    sMsgString.buffer5[251] = 0;
    InsertPrefix((char *)sMsgString.buffer5, colorPrefix, FALSE);
    Record(37, sMsgString.buffer5, 12);
    guard = gNativeTransformGuard;
    Record(38, &guard, sizeof(guard));

    for (index = 0; index < 255; index++)
        sMsgString.buffer1[index] = 'a';
    sMsgString.buffer1[255] = 0;
    result = StrInsertTact();
    Record(14, result + 252, 4);

    for (index = 0; index < 256; index++)
        sMsgString.buffer1[index] = 'a';
    sMsgString.buffer1[256] = 0;
    Record(15, StrInsertTact(), 12);

    SetName('X', 'Y', 'Z');
    for (index = 0; index < 253; index++)
        sMsgString.buffer1[index] = 'a';
    sMsgString.buffer1[253] = 0x80;
    sMsgString.buffer1[254] = 0x20;
    sMsgString.buffer1[255] = 0;
    Record(16, StrInsertTact(), 12);

    for (index = 0; index < 254; index++)
        sMsgString.buffer5[index] = 'a';
    sMsgString.buffer5[254] = 0;
    InsertPrefix((char *)sMsgString.buffer5, "P", FALSE);
    sample[0] = sMsgString.buffer5[0];
    sample[1] = sMsgString.buffer5[1];
    sample[2] = sMsgString.buffer5[253];
    sample[3] = sMsgString.buffer5[254];
    sample[4] = sMsgString.buffer5[255];
    Record(17, sample, 5);

    for (index = 0; index < 255; index++)
        sMsgString.buffer5[index] = 'a';
    sMsgString.buffer5[255] = 0;
    InsertPrefix((char *)sMsgString.buffer5, "P", FALSE);
    Record(18, sMsgString.buffer5, 12);

    for (index = 0; index < 285; index++)
    {
        sMsgString.buffer1[2 * index] = 0x80;
        sMsgString.buffer1[2 * index + 1] = 0x20;
    }
    sMsgString.buffer1[570] = 0;
    sMsgString.buffer4[0] = 0x6B;
    Record(19, StringInsertSpecialPrefixByCtrl(), 12);
    sMsgString.buffer1[568] = 0;
    result = StringInsertSpecialPrefixByCtrl();
    for (index = 0; index < 3; index++)
    {
        sample[index] = result[index];
        sample[index + 3] = result[849 + index];
    }
    sample[6] = result[852];
    Record(20, sample, 7);
    Record(23, sMsgString.buffer4, 1);
    guard = gNativeTransformGuard;
    Record(22, &guard, sizeof(guard));

    for (index = 0; index < sizeof(sMsgString); index++)
        ((u8 *)&sMsgString)[index] = 'a';
    ((u8 *)&sMsgString)[sizeof(sMsgString) - 1] = 0x10;
    Record(32, StrInsertTact(), 12);
    guard = gNativeTransformGuard;
    Record(33, &guard, sizeof(guard));
#endif

    gInitialCacheProbe.phase = 2;
    for (;;)
        __asm__ volatile ("nop");
}
