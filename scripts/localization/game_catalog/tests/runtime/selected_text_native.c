#include "global.h"
#include <stddef.h>
#include "bm.h"
#include "bmitem.h"
#include "bmunit.h"
#include "bmlib.h"
#include "localized_game_text.h"
#include "text_utf8.h"
#include "selected_msg.h"

#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
#include "game_localization_catalog.h"
#endif

struct ItemData CONST_DATA gItemData[2] =
{
    { .nameTextId = MSG_NATIVE_ZERO },
    { .nameTextId = MSG_NATIVE_ITEM, .number = 1 }
};

struct NativeTextRecord
{
    u32 locale;
    u32 id;
    u32 status;
    u32 length;
    u32 consumer;
    u32 guardBefore;
    char bytes[96];
    u32 guardAfter;
    u32 tokenCount;
    u32 controlCount;
    u8 controls[16];
};

struct NativeTextProbe
{
    u32 phase;
    u32 count;
    u32 msgCount;
    u32 localeMask;
    u32 itemStructBytes;
    u32 itemNameOffset;
    u32 retainedId;
    u32 msgBufferBytes;
    u32 decodeRamAddress;
    u32 copiedCodeBytes;
    u32 localeSelectionOk;
    u32 generatedTargetCount;
    struct NativeTextRecord records[16];
};

volatile struct NativeTextProbe gNativeTextProbe;
extern void (*DecodeStringRAMFunc)(const char *, char *);
extern const u8 ARMCodeToCopy_Start[];
extern const u8 ARMCodeToCopy_End[];

typedef char NativeThreeMessages[(MSG_COUNT == 3) ? 1 : -1];
typedef char NativeItemNameLayout[
    (offsetof(struct ItemData, nameTextId) == 0) ? 1 : -1];

static void Record(int id, int consumer, const char *text)
{
    volatile struct NativeTextRecord *record;
    u32 i;
#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
    struct TextUtf8Token token;
    const char *cursor;
    const char *next;
#endif

    record = &gNativeTextProbe.records[gNativeTextProbe.count++];
    record->locale = ExpansionLocale_GetCurrent();
    record->id = id;
    record->consumer = consumer;
#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
    record->status = LocalizedGameText_GetLastStatus();
#endif
    for (i = 0; i + 1 < sizeof(record->bytes) && text[i] != 0; i++)
        record->bytes[i] = text[i];
    record->bytes[i] = 0;
    record->length = i;
#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
    cursor = text;
    while ((u32)(cursor - text) <= i)
    {
        next = TextUtf8_NextBounded(cursor, i + 1 - (u32)(cursor - text), &token);
        if (token.kind == TEXT_UTF8_TOKEN_END)
            break;
        if (token.kind == TEXT_UTF8_TOKEN_INVALID || next == cursor)
        {
            record->tokenCount = 0xFFFFFFFF;
            break;
        }
        record->tokenCount++;
        if (token.kind == TEXT_UTF8_TOKEN_CONTROL
            && record->controlCount < sizeof(record->controls))
            record->controls[record->controlCount++] = token.control;
        cursor = next;
    }
#endif
}

static void RunActiveSet(void)
{
    char buffer[96];
    const char *text;

    text = GetStringFromIndex(MSG_NATIVE_PAGES);
    Record(MSG_NATIVE_PAGES, 0, text);
    text = GetStringFromIndex(MSG_NATIVE_ZERO);
    Record(MSG_NATIVE_ZERO, 0, text);
    text = GetItemName(1);
    Record(GetItemData(1)->nameTextId, 1, text);
#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
    text = GetStringFromIndexInBufferWithLimit(
        MSG_NATIVE_PAGES, buffer, sizeof(buffer));
#else
    text = GetStringFromIndexInBuffer(MSG_NATIVE_PAGES, buffer);
#endif
    Record(MSG_NATIVE_PAGES, 2, text);
}

void AgbMain(void)
{
#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
    const char *text;
    struct
    {
        u32 before;
        char bytes[4];
        u32 after;
    } bounded;
#endif

    gNativeTextProbe.phase = 1;
    gNativeTextProbe.msgCount = MSG_COUNT;
    gNativeTextProbe.localeMask = FE8_EXPANSION_ENABLED_LOCALE_MASK;
    gNativeTextProbe.itemStructBytes = sizeof(struct ItemData);
    gNativeTextProbe.itemNameOffset = offsetof(struct ItemData, nameTextId);
    gNativeTextProbe.retainedId = GetItemData(1)->nameTextId;
    gNativeTextProbe.msgBufferBytes = sizeof(struct MsgBuffer);
    gNativeTextProbe.generatedTargetCount = FE8_GAME_LOCALIZATION_TARGET_COUNT;
    StoreRoutinesToIRAM();
    gNativeTextProbe.decodeRamAddress = (u32)DecodeStringRAMFunc;
    gNativeTextProbe.copiedCodeBytes = ARMCodeToCopy_End - ARMCodeToCopy_Start;
    gNativeTextProbe.phase = 2;
    gNativeTextProbe.localeSelectionOk = ExpansionLocale_SetCurrent(EXPANSION_LOCALE_EN);
    RunActiveSet();
#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
    gNativeTextProbe.phase = 3;
    gNativeTextProbe.localeSelectionOk &= ExpansionLocale_SetCurrent(EXPANSION_LOCALE_JA);
    RunActiveSet();
    gNativeTextProbe.localeSelectionOk &= ExpansionLocale_SetCurrent(EXPANSION_LOCALE_EN);
    text = GetStringFromIndex(MSG_NATIVE_PAGES);
    Record(MSG_NATIVE_PAGES, 0, text);
    text = GetStringFromIndex(MSG_COUNT);
    Record(MSG_COUNT, 0, text);
    text = GetStringFromIndex(-1);
    Record(-1, 0, text);
    gNativeTextProbe.localeSelectionOk &= ExpansionLocale_SetCurrent(EXPANSION_LOCALE_JA);
    bounded.before = 0x13579BDF;
    bounded.after = 0x2468ACE0;
    text = GetStringFromIndexInBufferWithLimit(
        MSG_NATIVE_PAGES, bounded.bytes, sizeof(bounded.bytes));
    Record(MSG_NATIVE_PAGES, 3, text);
    gNativeTextProbe.records[gNativeTextProbe.count - 1].guardBefore = bounded.before;
    gNativeTextProbe.records[gNativeTextProbe.count - 1].guardAfter = bounded.after;
#else
    gNativeTextProbe.localeSelectionOk &= !ExpansionLocale_SetCurrent(EXPANSION_LOCALE_JA);
#endif
    gNativeTextProbe.phase = 0x314D0E;
    for (;;)
        __asm__ volatile ("nop");
}
