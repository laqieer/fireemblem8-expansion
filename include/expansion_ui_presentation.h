#ifndef GUARD_EXPANSION_UI_PRESENTATION_H
#define GUARD_EXPANSION_UI_PRESENTATION_H

#include "global.h"

#define EXPANSION_UI_PRESENTATION_KIND_CHAPTER_TITLE 0
#define EXPANSION_UI_PRESENTATION_KIND_SCREEN 1

#define EXPANSION_UI_PRESENTATION_FLAG_HAS_ASSET 0x01
#define EXPANSION_UI_PRESENTATION_FLAG_ASSET_REQUIRED 0x02

struct ExpansionUiPresentationManifest
{
    /* 00 */ u8 id;
    /* 01 */ u8 kind;
    /* 02 */ u8 chapterId;
    /* 03 */ u8 flags;
    /* 04 */ u16 titleMsgId;
    /* 06 */ u16 assetId;
    /* 08 */ u16 requiredVramBytes;
    /* 0A */ u8 requiredPaletteSlots;
    /* 0B */ u8 requiredOamEntries;
    /* 0C */ char const *fallbackText;
};

extern struct ExpansionUiPresentationManifest const gExpansionUiPresentationManifest[];
extern u8 const gExpansionUiPresentationManifestCount;

struct ExpansionUiPresentationManifest const *ExpansionUiPresentation_Find(u8 id);
struct ExpansionUiPresentationManifest const *ExpansionUiPresentation_FindChapterTitle(u8 chapterId);
bool8 ExpansionUiPresentation_ValidateManifest(void);
bool8 ExpansionUiPresentation_ResolveTitle(
    u8 contextId,
    u16 *outTitleMsgId,
    char const **outFallbackText);

#endif
