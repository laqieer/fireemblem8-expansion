#ifndef GUARD_LOCALIZED_GAME_TEXT_H
#define GUARD_LOCALIZED_GAME_TEXT_H

#include "gba/types.h"
#include "expansion_config.h"
#include "expansion_locale.h"

#define FE8_LOCALIZED_GAME_TEXT_TARGET_CAPACITY 0x1600u
#define FE8_LOCALIZED_GAME_TEXT_TRANSFORM_OUTPUT_BYTES 0x400u
#define FE8_LOCALIZED_GAME_TEXT_TRANSFORM_INSERTION_BYTES 0x100u

#define FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER1_BYTES 0x555u
#define FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER2_BYTES 0x555u
#define FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER3_BYTES 0x356u
#define FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER4_BYTES 0x100u
#define FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER5_BYTES 0x100u

#if defined(MODERN) && ((FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x7Eu) != 0)
#define FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED 1
#else
#define FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED 0
#endif

#define FE8_LOCALIZED_GAME_TEXT_LEGACY_MSG_BUFFER_BYTES \
    (FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER1_BYTES \
        + FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER2_BYTES \
        + FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER3_BYTES \
        + FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER4_BYTES \
        + FE8_LOCALIZED_GAME_TEXT_LEGACY_BUFFER5_BYTES)

#if !FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
#define FE8_LOCALIZED_GAME_TEXT_MSG_BUFFER_BYTES \
    FE8_LOCALIZED_GAME_TEXT_LEGACY_MSG_BUFFER_BYTES
#else
extern const u32 gLocalizedGameTextStorageCapacity;
#endif

#define LOCALIZED_GAME_TEXT_STATIC_ASSERT(cond, tag) \
    typedef char localized_game_text_static_assert_##tag[(cond) ? 1 : -1]

LOCALIZED_GAME_TEXT_STATIC_ASSERT(
    FE8_LOCALIZED_GAME_TEXT_LEGACY_MSG_BUFFER_BYTES == 0x1000u,
    legacy_msg_buffer_is_exact_4k);

#define LOCALIZED_GAME_TEXT_MARKER_OVERFLOW "<!LOC_OVF!>"
#define LOCALIZED_GAME_TEXT_MARKER_CORRUPT  "<!LOC_BAD!>"
#define LOCALIZED_GAME_TEXT_MARKER_INVALID  "<!LOC_INV!>"
#define LOCALIZED_GAME_TEXT_MARKER_UNBOUNDED "<!LOC_CAP!>"

/* Explicit resolution status for renderer/integration work:
 * - ENGLISH_DEFAULT: the modern English bundle decoded for English/qps.
 * - ENGLISH_FALLBACK_*: a CJK request decoded the shared English bundle.
 * - DECODE_*: a generated entry was attempted and wrote a visible marker
 *   instead of silently succeeding. */
enum LocalizedGameTextStatus
{
    LOCALIZED_GAME_TEXT_STATUS_ENGLISH_DEFAULT = 0,
    LOCALIZED_GAME_TEXT_STATUS_OK = 1,
    LOCALIZED_GAME_TEXT_STATUS_ENGLISH_FALLBACK_ABSENT = 2,
    LOCALIZED_GAME_TEXT_STATUS_ENGLISH_FALLBACK_UNPOPULATED = 3,
    LOCALIZED_GAME_TEXT_STATUS_DECODE_OVERFLOW = 4,
    LOCALIZED_GAME_TEXT_STATUS_DECODE_CORRUPT = 5,
    LOCALIZED_GAME_TEXT_STATUS_DECODE_INVALID = 6,
    LOCALIZED_GAME_TEXT_STATUS_LEGACY_BUFFER_UNBOUNDED = 7
};

enum LocalizedGameTextDisplaySurface
{
    LOCALIZED_GAME_TEXT_DISPLAY_CHARACTER_NAME_40 = 1,
    LOCALIZED_GAME_TEXT_DISPLAY_CLASS_NAME_64 = 2,
    LOCALIZED_GAME_TEXT_DISPLAY_ITEM_NAME_56 = 3
};

enum LocalizedGameTextStatus LocalizedGameText_ResolveCurrentToBuffer(
    int msgIndex,
    char *buffer,
    u32 bufferCapacity,
    u32 *outDecodedLength);
enum LocalizedGameTextStatus LocalizedGameText_ResolveCurrentToUnboundedBuffer(
    int msgIndex,
    char *buffer,
    u32 *outDecodedLength);

void LocalizedGameText_InvalidateCache(void);
enum LocalizedGameTextStatus LocalizedGameText_GetLastStatus(void);
#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED
struct CharacterData;
struct ClassData;

const char * LocalizedGameText_GetDisplayAliasForWidth(
    int msgIndex,
    enum LocalizedGameTextDisplaySurface surface,
    int maxPixels,
    int canonicalPixels);
char * GetCharacterDisplayNameForWidth(
    const struct CharacterData *character,
    int maxPixels);
char * GetClassDisplayNameForWidth(
    const struct ClassData *classData,
    int maxPixels);
char * GetItemDisplayNameForWidth(int item, int maxPixels);
#endif

#endif /* GUARD_LOCALIZED_GAME_TEXT_H */
