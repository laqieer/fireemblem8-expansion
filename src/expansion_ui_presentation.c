#include "global.h"

#include "expansion_ui_presentation.h"
#include "expansion_ui_presentation_manifest.h"

static bool8 ResourceBoundsAreValid(struct ExpansionUiPresentationManifest const *entry)
{
    if (entry->requiredVramBytes > 0x8000)
        return FALSE;
    if (entry->requiredPaletteSlots > 16)
        return FALSE;
    if (entry->requiredOamEntries > 128)
        return FALSE;
    if ((entry->flags & EXPANSION_UI_PRESENTATION_FLAG_ASSET_REQUIRED)
        && !(entry->flags & EXPANSION_UI_PRESENTATION_FLAG_HAS_ASSET))
        return FALSE;

    return TRUE;
}

struct ExpansionUiPresentationManifest const *ExpansionUiPresentation_Find(u8 id)
{
    if (id >= gExpansionUiPresentationManifestCount)
        return NULL;

    return &gExpansionUiPresentationManifest[id];
}

struct ExpansionUiPresentationManifest const *ExpansionUiPresentation_FindChapterTitle(u8 chapterId)
{
    u8 i;

    for (i = 0; i < gExpansionUiPresentationManifestCount; ++i)
    {
        if (gExpansionUiPresentationManifest[i].kind == EXPANSION_UI_PRESENTATION_KIND_CHAPTER_TITLE
            && gExpansionUiPresentationManifest[i].chapterId == chapterId)
            return &gExpansionUiPresentationManifest[i];
    }

    return NULL;
}

bool8 ExpansionUiPresentation_ValidateManifest(void)
{
    u8 i;

    for (i = 0; i < gExpansionUiPresentationManifestCount; ++i)
    {
        if (gExpansionUiPresentationManifest[i].id != i)
            return FALSE;
        if (!ResourceBoundsAreValid(&gExpansionUiPresentationManifest[i]))
            return FALSE;
        if (gExpansionUiPresentationManifest[i].fallbackText == NULL)
            return FALSE;
    }

    return TRUE;
}

bool8 ExpansionUiPresentation_ResolveTitle(
    u8 contextId,
    u16 *outTitleMsgId,
    char const **outFallbackText)
{
    struct ExpansionUiPresentationManifest const *entry = ExpansionUiPresentation_Find(contextId);

    if (entry == NULL || !ResourceBoundsAreValid(entry))
        return FALSE;

    if ((entry->flags & EXPANSION_UI_PRESENTATION_FLAG_ASSET_REQUIRED)
        && !(entry->flags & EXPANSION_UI_PRESENTATION_FLAG_HAS_ASSET))
        return FALSE;

    if (outTitleMsgId != NULL)
        *outTitleMsgId = entry->titleMsgId;
    if (outFallbackText != NULL)
        *outFallbackText = entry->fallbackText;

    return TRUE;
}
