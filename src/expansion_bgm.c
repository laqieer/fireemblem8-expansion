#include "global.h"
#include "expansion_bgm.h"
#include "bmitem.h"
#include "bmunit.h"
#include "chapterdata.h"
#include "eventinfo.h"
#include "bmsave.h"
#include "constants/songs.h"

#ifndef FE8_ARCHIVAL_BUILD
#include "expansion_debugtools.h"
#endif

static int ExpansionBgm_ContextIsValid(enum ExpansionBgmContext context)
{
    return context >= EXPANSION_BGM_CONTEXT_MAP_PHASE
        && context < EXPANSION_BGM_CONTEXT_COUNT;
}

#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_DEBUGTOOLS_ENABLED
struct ExpansionBgmPreviewState
{
    enum ExpansionBgmPreviewOwner owner;
    enum ExpansionBgmContext priorContext;
    struct SoundBgmContext priorSound;
    u8 hasPlayed;
};

SECTION("debugtools_contributor_data") static enum ExpansionBgmContext
    sExpansionBgmCurrentContext =
    EXPANSION_BGM_CONTEXT_MAP_PHASE;
SECTION("debugtools_contributor_data") static struct ExpansionBgmPreviewState
    sExpansionBgmPreviewState = {0};

static void ExpansionBgm_RememberContext(enum ExpansionBgmContext context)
{
    if (ExpansionBgm_ContextIsValid(context))
        sExpansionBgmCurrentContext = context;
}
#else
#define ExpansionBgm_RememberContext(context) ((void)(context))
#endif

static int ExpansionBgm_ActionIsValid(enum ExpansionBgmAction action)
{
    return action >= EXPANSION_BGM_ACTION_DANCE
        && action < EXPANSION_BGM_ACTION_COUNT;
}

static int ExpansionBgm_VariantMatches(
    const struct ExpansionBgmVariant *variant,
    const struct ExpansionBgmContextRequest *request)
{
    if (!ExpansionBgm_ContextIsValid(request->context))
        return FALSE;
    if (variant->context != request->context)
        return FALSE;
    if ((variant->matchMask & EXPANSION_BGM_VARIANT_MATCH_CHAPTER)
        && variant->chapterId != request->chapterId)
        return FALSE;
    if ((variant->matchMask & EXPANSION_BGM_VARIANT_MATCH_FLAG)
        && CheckFlag(variant->flagId) != (variant->whenFlagSet != 0))
        return FALSE;
    return TRUE;
}

int ExpansionBgm_Resolve(const struct ExpansionBgmContextRequest *request)
{
    u32 i;
    int bestSong;
    int bestPriority;

    if (request == NULL)
        return SONG_NONE;

    if (request->hasExplicitSong)
        return request->explicitSong;

    if (request->fallbackSong == SONG_NONE)
        return SONG_NONE;

    bestSong = request->fallbackSong;
    bestPriority = -1;

    for (i = 0; i < gExpansionBgmVariantCount; ++i)
    {
        const struct ExpansionBgmVariant *variant = &gExpansionBgmVariants[i];

        if (ExpansionBgm_VariantMatches(variant, request)
            && (int)variant->priority > bestPriority)
        {
            bestPriority = variant->priority;
            bestSong = variant->songId;
        }
    }

    return bestSong;
}

static int ExpansionBgm_SelectorMatches(
    const struct ExpansionBgmActionSelector *selector,
    enum ExpansionBgmAction action,
    const struct Unit *unit,
    int item,
    enum ExpansionBgmStaffKind staffKind,
    int *specificity)
{
    int value;
    int score = 0;

    if (selector->action != action)
        return FALSE;

    if (action == EXPANSION_BGM_ACTION_STAFF)
    {
        value = staffKind;
        if ((selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_STAFF_KIND)
            && selector->staffKind != value)
            return FALSE;
        if (selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_STAFF_KIND)
            score += 8;
        if ((selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_ITEM)
            && selector->itemId != ITEM_INDEX(item))
            return FALSE;
        if (selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_ITEM)
            score += 4;
        if (value == EXPANSION_BGM_STAFF_NONE)
            return FALSE;
    }
    else
    {
        if (selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_STAFF_KIND)
            return FALSE;
        if ((selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_ITEM)
            && selector->itemId != ITEM_INDEX(item))
            return FALSE;
        if (selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_ITEM)
            score += 4;
    }

    if (selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_CHARACTER)
    {
        if (unit == NULL || UNIT_CHAR_ID(unit) != selector->characterId)
            return FALSE;
        score += 2;
    }

    if (selector->matchMask & EXPANSION_BGM_SELECTOR_MATCH_CLASS)
    {
        if (unit == NULL || UNIT_CLASS_ID(unit) != selector->classId)
            return FALSE;
        score += 1;
    }

    *specificity = score;
    return TRUE;
}

int ExpansionBgm_SelectActionSong(
    enum ExpansionBgmAction action,
    const struct Unit *unit,
    int item,
    enum ExpansionBgmStaffKind staffKind,
    int legacySong)
{
    u32 i;
    int bestSong = legacySong;
    int bestPriority = -1;
    int bestSpecificity = -1;

    if (legacySong == SONG_NONE || !ExpansionBgm_ActionIsValid(action))
        return legacySong;

    for (i = 0; i < gExpansionBgmActionSelectorCount; ++i)
    {
        int specificity;
        const struct ExpansionBgmActionSelector *selector =
            &gExpansionBgmActionSelectors[i];

        if (!ExpansionBgm_SelectorMatches(
                selector, action, unit, item, staffKind, &specificity))
            continue;

        if ((int)selector->priority > bestPriority
            || ((int)selector->priority == bestPriority
                && specificity > bestSpecificity))
        {
            bestPriority = selector->priority;
            bestSpecificity = specificity;
            bestSong = selector->songId;
        }
    }

    return bestSong;
}

static struct ExpansionBgmContextRequest ExpansionBgm_MakeRequest(
    enum ExpansionBgmContext context,
    int fallbackSong)
{
    struct ExpansionBgmContextRequest request;

    request.context = context;
    request.chapterId = (u16)(u8)gPlaySt.chapterIndex;
    request.hasExplicitSong = FALSE;
    request.padding = 0;
    request.fallbackSong = fallbackSong;
    request.explicitSong = SONG_NONE;

    return request;
}

void ExpansionBgm_Start(
    enum ExpansionBgmContext context,
    int fallbackSong,
    int speed,
    struct MusicPlayerInfo *player)
{
    struct ExpansionBgmContextRequest request =
        ExpansionBgm_MakeRequest(context, fallbackSong);
    int songId;

    ExpansionBgm_RememberContext(context);

    if (fallbackSong == SONG_NONE)
    {
        StartOrChangeBgm(SONG_NONE, speed, player);
        return;
    }

    songId = ExpansionBgm_Resolve(&request);
    StartOrChangeBgm(songId, speed, player);
}

void ExpansionBgm_StartExplicit(
    enum ExpansionBgmContext context,
    int songId,
    int speed,
    struct MusicPlayerInfo *player)
{
    struct ExpansionBgmContextRequest request =
        ExpansionBgm_MakeRequest(context, SONG_NONE);

    ExpansionBgm_RememberContext(context);

    if (songId == SONG_NONE)
    {
        StartOrChangeBgm(SONG_NONE, speed, player);
        return;
    }

    request.hasExplicitSong = TRUE;
    request.explicitSong = songId;
    StartOrChangeBgm(ExpansionBgm_Resolve(&request), speed, player);
}

void ExpansionBgm_FadeInExplicit(
    enum ExpansionBgmContext context,
    int songId,
    int duration,
    struct MusicPlayerInfo *player)
{
    ExpansionBgm_RememberContext(context);
    if (songId != SONG_NONE)
        StartBgmFadeIn(songId, duration, player);
}

void ExpansionBgm_Change(
    enum ExpansionBgmContext context,
    int fallbackSong,
    int volumeInit,
    int volumeEnd,
    int duration,
    ProcPtr parent)
{
    struct ExpansionBgmContextRequest request =
        ExpansionBgm_MakeRequest(context, fallbackSong);
    int songId;

    ExpansionBgm_RememberContext(context);

    if (fallbackSong == SONG_NONE)
    {
        ChangeBgm(SONG_NONE, volumeInit, volumeEnd, duration, parent);
        return;
    }

    songId = ExpansionBgm_Resolve(&request);
    ChangeBgm(songId, volumeInit, volumeEnd, duration, parent);
}

void ExpansionBgm_ChangeExplicit(
    enum ExpansionBgmContext context,
    int songId,
    int volumeInit,
    int volumeEnd,
    int duration,
    ProcPtr parent)
{
    ExpansionBgm_RememberContext(context);
    ChangeBgm(songId, volumeInit, volumeEnd, duration, parent);
}

void ExpansionBgm_Override(enum ExpansionBgmContext context, int songId)
{
    ExpansionBgm_RememberContext(context);
    OverrideBgm(songId);
}

void ExpansionBgm_Restore(enum ExpansionBgmContext context, u16 speed)
{
    ExpansionBgm_RememberContext(context);
    _RestoreBgm(speed);
}

void ExpansionBgm_Continue(
    enum ExpansionBgmContext context,
    int songId,
    int speed,
    struct MusicPlayerInfo *player)
{
    struct ExpansionBgmContextRequest request;
    int resolvedSong;

    ExpansionBgm_RememberContext(context);

    if (songId == SONG_NONE)
        return;

    request = ExpansionBgm_MakeRequest(context, songId);
    resolvedSong = ExpansionBgm_Resolve(&request);
    if (resolvedSong == SONG_NONE)
        return;

    if (FE8_EXPANSION_BGM_CONTINUATION_POLICY
        == EXPANSION_BGM_CONTINUATION_RESTART)
    {
        StartBgmFadeIn(resolvedSong, speed, player);
        return;
    }

    if (FE8_EXPANSION_BGM_CONTINUATION_POLICY
        == EXPANSION_BGM_CONTINUATION_PRESERVE
        && (!IsBgmPlaying() || GetCurrentBgmSong() == resolvedSong))
        return;

    if (IsBgmPlaying() && GetCurrentBgmSong() == resolvedSong)
        return;

    StartOrChangeBgm(resolvedSong, speed, player);
}

int ExpansionBgm_GetContinuationPolicy(void)
{
    return FE8_EXPANSION_BGM_CONTINUATION_POLICY;
}

#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_DEBUGTOOLS_ENABLED
bool ExpansionBgm_AcquirePreview(enum ExpansionBgmPreviewOwner owner)
{
    if (owner == EXPANSION_BGM_PREVIEW_OWNER_NONE)
        return FALSE;

    if (sExpansionBgmPreviewState.owner != EXPANSION_BGM_PREVIEW_OWNER_NONE)
        return FALSE;

    sExpansionBgmPreviewState.owner = owner;
    sExpansionBgmPreviewState.priorContext = sExpansionBgmCurrentContext;
    Sound_CaptureBgmContext(&sExpansionBgmPreviewState.priorSound);
    sExpansionBgmPreviewState.hasPlayed = FALSE;
    return TRUE;
}

bool ExpansionBgm_PreviewSong(enum ExpansionBgmPreviewOwner owner, int songId)
{
    if (owner == EXPANSION_BGM_PREVIEW_OWNER_NONE
        || sExpansionBgmPreviewState.owner != owner)
        return FALSE;

    if (songId == SONG_NONE || !IsSoundRoomSongIdValid(songId))
        return FALSE;

    if (!Sound_StartTransientBgm(songId, NULL))
        return FALSE;

    sExpansionBgmPreviewState.hasPlayed = TRUE;
    return TRUE;
}

int ExpansionBgm_ReleasePreview(enum ExpansionBgmPreviewOwner owner)
{
    int result = EXPANSION_BGM_PREVIEW_RELEASED_IDLE;

    if (owner == EXPANSION_BGM_PREVIEW_OWNER_NONE
        || sExpansionBgmPreviewState.owner != owner)
        return EXPANSION_BGM_PREVIEW_RELEASE_ERROR;

    if (sExpansionBgmPreviewState.hasPlayed)
    {
        if (!Sound_RestoreBgmContext(&sExpansionBgmPreviewState.priorSound, NULL))
            result = EXPANSION_BGM_PREVIEW_RELEASE_ERROR;
        else
            result = EXPANSION_BGM_PREVIEW_RESTORED;
    }

    sExpansionBgmCurrentContext = sExpansionBgmPreviewState.priorContext;
    sExpansionBgmPreviewState.owner = EXPANSION_BGM_PREVIEW_OWNER_NONE;
    sExpansionBgmPreviewState.priorContext = EXPANSION_BGM_CONTEXT_MAP_PHASE;
    sExpansionBgmPreviewState.hasPlayed = FALSE;

    return result;
}

enum ExpansionBgmPreviewOwner ExpansionBgm_GetPreviewOwner(void)
{
    return sExpansionBgmPreviewState.owner;
}

enum ExpansionBgmContext ExpansionBgm_GetCurrentContext(void)
{
    return sExpansionBgmCurrentContext;
}
#endif
