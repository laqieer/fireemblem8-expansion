#include "global.h"

#include "localized_game_text.h"

#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED

#include "game_localization_catalog.h"
#include "localized_text_codec.h"

static int LocalizedGameText_UsesLocalizedCatalog(ExpansionLocaleId locale)
{
    return locale == EXPANSION_LOCALE_JA
        || locale == EXPANSION_LOCALE_ZH_HANS
        || locale == EXPANSION_LOCALE_FR
        || locale == EXPANSION_LOCALE_DE
        || locale == EXPANSION_LOCALE_ES
        || locale == EXPANSION_LOCALE_IT;
}

struct LocalizedGameTextSelection
{
    const struct GameLocalizationLocaleCatalog *catalog;
    const struct GameLocalizationCatalogEntry *entry;
    enum LocalizedGameTextStatus successStatus;
};

static void LocalizedGameText_WriteMarker(
    char *buffer,
    u32 bufferCapacity,
    const char *marker,
    u32 *outDecodedLength)
{
    u32 i;

    if (outDecodedLength != 0)
        *outDecodedLength = 0;

    if (buffer == 0 || bufferCapacity == 0)
        return;

    i = 0;
    while (i + 1 < bufferCapacity && marker[i] != '\0')
    {
        buffer[i] = marker[i];
        i++;
    }

    buffer[i] = '\0';
    if (outDecodedLength != 0)
        *outDecodedLength = i + 1;
}

static const struct GameLocalizationLocaleCatalog *LocalizedGameText_GetCatalog(
    ExpansionLocaleId locale)
{
    const struct GameLocalizationLocaleCatalog *catalog;
    u32 catalogIndex;

    if (!LocalizedGameText_UsesLocalizedCatalog(locale))
        return 0;

    switch (locale)
    {
    case EXPANSION_LOCALE_JA:
        catalogIndex = GAME_LOCALIZATION_LOCALE_JA;
        break;

    case EXPANSION_LOCALE_ZH_HANS:
        catalogIndex = GAME_LOCALIZATION_LOCALE_ZH_HANS;
        break;

#ifdef GAME_LOCALIZATION_LOCALE_FR
    case EXPANSION_LOCALE_FR:
        catalogIndex = GAME_LOCALIZATION_LOCALE_FR;
        break;
#endif

#ifdef GAME_LOCALIZATION_LOCALE_DE
    case EXPANSION_LOCALE_DE:
        catalogIndex = GAME_LOCALIZATION_LOCALE_DE;
        break;
#endif

#ifdef GAME_LOCALIZATION_LOCALE_ES
    case EXPANSION_LOCALE_ES:
        catalogIndex = GAME_LOCALIZATION_LOCALE_ES;
        break;
#endif

#ifdef GAME_LOCALIZATION_LOCALE_IT
    case EXPANSION_LOCALE_IT:
        catalogIndex = GAME_LOCALIZATION_LOCALE_IT;
        break;
#endif

    default:
        return 0;
    }

    catalog = gGameLocalizationCatalogs[catalogIndex];
    if (catalog == 0 || catalog->entries == 0 || catalog->entryCount == 0)
        return 0;

    return catalog;
}

static enum LocalizedGameTextStatus LocalizedGameText_Select(
    int msgIndex,
    struct LocalizedGameTextSelection *selection)
{
    ExpansionLocaleId locale;
    const struct GameLocalizationLocaleCatalog *catalog;
    const struct GameLocalizationCatalogEntry *entry;
    enum LocalizedGameTextStatus successStatus;

    if (selection == 0 || msgIndex < 0
        || (u32)msgIndex >= FE8_GAME_LOCALIZATION_TARGET_COUNT)
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_INVALID;

    locale = ExpansionLocale_GetCurrent();
    if (!LocalizedGameText_UsesLocalizedCatalog(locale))
    {
        catalog = &gGameLocalizationEnglishCatalog;
        successStatus = LOCALIZED_GAME_TEXT_STATUS_ENGLISH_DEFAULT;
    }
    else
    {
        catalog = LocalizedGameText_GetCatalog(locale);
        if (catalog == 0)
        {
            catalog = &gGameLocalizationEnglishCatalog;
            successStatus =
                LOCALIZED_GAME_TEXT_STATUS_ENGLISH_FALLBACK_UNPOPULATED;
        }
        else if ((u32)msgIndex >= catalog->entryCount
            || !catalog->entries[msgIndex].present
            || catalog->entries[msgIndex].data == 0)
        {
            catalog = &gGameLocalizationEnglishCatalog;
            successStatus = LOCALIZED_GAME_TEXT_STATUS_ENGLISH_FALLBACK_ABSENT;
        }
        else
        {
            successStatus = LOCALIZED_GAME_TEXT_STATUS_OK;
        }
    }

    if (catalog->entries == 0 || (u32)msgIndex >= catalog->entryCount)
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_INVALID;

    entry = &catalog->entries[msgIndex];
    if (!entry->present || entry->data == 0)
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_INVALID;

    selection->catalog = catalog;
    selection->entry = entry;
    selection->successStatus = successStatus;
    return LOCALIZED_GAME_TEXT_STATUS_OK;
}

static enum LocalizedGameTextStatus LocalizedGameText_MapCodecStatus(
    enum LocalizedTextCodecStatus status)
{
    switch (status)
    {
    case LOCALIZED_TEXT_CODEC_OK:
        return LOCALIZED_GAME_TEXT_STATUS_OK;

    case LOCALIZED_TEXT_CODEC_OUTPUT_OVERFLOW:
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_OVERFLOW;

    case LOCALIZED_TEXT_CODEC_INVALID_ARGUMENT:
    case LOCALIZED_TEXT_CODEC_INVALID_ROOT:
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_INVALID;

    case LOCALIZED_TEXT_CODEC_INVALID_NODE:
    case LOCALIZED_TEXT_CODEC_INVALID_SYMBOL:
    case LOCALIZED_TEXT_CODEC_TRUNCATED_INPUT:
    case LOCALIZED_TEXT_CODEC_MISSING_TERMINATOR:
    default:
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_CORRUPT;
    }
}

static enum LocalizedGameTextStatus LocalizedGameText_DecodeSelection(
    const struct LocalizedGameTextSelection *selection,
    char *buffer,
    u32 bufferCapacity,
    u32 *outDecodedLength)
{
    const struct GameLocalizationLocaleCatalog *catalog;
    const struct GameLocalizationCatalogEntry *entry;
    enum LocalizedTextCodecStatus codecStatus;
    enum LocalizedGameTextStatus mappedStatus;
    u32 localDecodedLength;
    u32 *decodedLengthOut;

    if (selection == 0 || buffer == 0 || bufferCapacity == 0)
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_INVALID;

    catalog = selection->catalog;
    entry = selection->entry;

    if (entry->compressedSize == 0 || entry->bitLength == 0
        || entry->maxDecodedBytes == 0 || catalog->nodes == 0
        || catalog->nodeCount == 0 || catalog->rootIndex >= catalog->nodeCount)
    {
        LocalizedGameText_WriteMarker(
            buffer, bufferCapacity, LOCALIZED_GAME_TEXT_MARKER_INVALID, outDecodedLength);
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_INVALID;
    }

    if (entry->maxDecodedBytes > bufferCapacity)
    {
        LocalizedGameText_WriteMarker(
            buffer, bufferCapacity, LOCALIZED_GAME_TEXT_MARKER_OVERFLOW, outDecodedLength);
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_OVERFLOW;
    }

    decodedLengthOut = outDecodedLength;
    if (decodedLengthOut == 0)
        decodedLengthOut = &localDecodedLength;

    codecStatus = LocalizedTextCodec_Decode(
        catalog->nodes,
        catalog->nodeCount,
        catalog->rootIndex,
        entry->data,
        entry->compressedSize,
        entry->bitLength,
        (u8 *)buffer,
        bufferCapacity,
        decodedLengthOut);

    mappedStatus = LocalizedGameText_MapCodecStatus(codecStatus);
    if (mappedStatus == LOCALIZED_GAME_TEXT_STATUS_OK)
        return selection->successStatus;

    if (mappedStatus == LOCALIZED_GAME_TEXT_STATUS_DECODE_OVERFLOW)
    {
        LocalizedGameText_WriteMarker(
            buffer, bufferCapacity, LOCALIZED_GAME_TEXT_MARKER_OVERFLOW, outDecodedLength);
        return mappedStatus;
    }

    if (mappedStatus == LOCALIZED_GAME_TEXT_STATUS_DECODE_CORRUPT)
    {
        LocalizedGameText_WriteMarker(
            buffer, bufferCapacity, LOCALIZED_GAME_TEXT_MARKER_CORRUPT, outDecodedLength);
        return mappedStatus;
    }

    LocalizedGameText_WriteMarker(
        buffer, bufferCapacity, LOCALIZED_GAME_TEXT_MARKER_INVALID, outDecodedLength);
    return mappedStatus;
}

enum LocalizedGameTextStatus LocalizedGameText_ResolveCurrentToBuffer(
    int msgIndex,
    char *buffer,
    u32 bufferCapacity,
    u32 *outDecodedLength)
{
    struct LocalizedGameTextSelection selection;
    enum LocalizedGameTextStatus status;

    if (outDecodedLength != 0)
        *outDecodedLength = 0;

    status = LocalizedGameText_Select(msgIndex, &selection);
    if (status != LOCALIZED_GAME_TEXT_STATUS_OK)
    {
        LocalizedGameText_WriteMarker(
            buffer,
            bufferCapacity,
            LOCALIZED_GAME_TEXT_MARKER_INVALID,
            outDecodedLength);
        return status;
    }

    return LocalizedGameText_DecodeSelection(
        &selection, buffer, bufferCapacity, outDecodedLength);
}

enum LocalizedGameTextStatus LocalizedGameText_ResolveCurrentToUnboundedBuffer(
    int msgIndex,
    char *buffer,
    u32 *outDecodedLength)
{
    struct LocalizedGameTextSelection selection;
    enum LocalizedGameTextStatus status;

    if (outDecodedLength != 0)
        *outDecodedLength = 0;

    if (buffer == 0)
        return LOCALIZED_GAME_TEXT_STATUS_DECODE_INVALID;

    status = LocalizedGameText_Select(msgIndex, &selection);
    if (status != LOCALIZED_GAME_TEXT_STATUS_OK)
    {
        buffer[0] = '\0';
        return status;
    }

    return LocalizedGameText_DecodeSelection(
        &selection,
        buffer,
        selection.entry->maxDecodedBytes,
        outDecodedLength);
}

const char * LocalizedGameText_GetDisplayAliasForWidth(
    int msgIndex,
    enum LocalizedGameTextDisplaySurface surface,
    int maxPixels,
    int canonicalPixels)
{
    const struct GameLocalizationDisplayAlias *aliases;
    u32 aliasCount;
    u32 i;
    ExpansionLocaleId locale;
    int surfacePixels;

    switch (surface)
    {
    case LOCALIZED_GAME_TEXT_DISPLAY_CHARACTER_NAME_40:
        surfacePixels = 40;
        break;

    case LOCALIZED_GAME_TEXT_DISPLAY_CLASS_NAME_64:
        surfacePixels = 64;
        break;

    case LOCALIZED_GAME_TEXT_DISPLAY_ITEM_NAME_56:
        surfacePixels = 56;
        break;

    default:
        return 0;
    }

    if (maxPixels != surfacePixels || canonicalPixels <= maxPixels)
        return 0;

    locale = ExpansionLocale_GetCurrent();
    if (locale == EXPANSION_LOCALE_JA)
    {
#if GAME_LOCALIZATION_JA_ENABLED
        aliases = gGameLocalizationJaDisplayAliases;
        aliasCount = GAME_LOCALIZATION_JA_DISPLAY_ALIAS_COUNT;
#else
        return 0;
#endif
    }
    else if (locale == EXPANSION_LOCALE_ZH_HANS)
    {
#if GAME_LOCALIZATION_ZH_HANS_ENABLED
        aliases = gGameLocalizationZhHansDisplayAliases;
        aliasCount = GAME_LOCALIZATION_ZH_HANS_DISPLAY_ALIAS_COUNT;
#else
        return 0;
#endif
    }
    else
    {
        return 0;
    }

    for (i = 0; i < aliasCount; i++)
    {
        if (aliases[i].targetId == msgIndex && aliases[i].surface == surface)
            return aliases[i].text;
    }
    return 0;
}

#endif /* FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED */
