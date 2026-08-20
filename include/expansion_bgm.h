#ifndef GUARD_EXPANSION_BGM_H
#define GUARD_EXPANSION_BGM_H

#include "global.h"
#include "soundwrapper.h"

struct Unit;

enum ExpansionBgmContext
{
    EXPANSION_BGM_CONTEXT_MAP_PHASE = 0,
    EXPANSION_BGM_CONTEXT_BATTLE,
    EXPANSION_BGM_CONTEXT_PREPARATION,
    EXPANSION_BGM_CONTEXT_MENU,
    EXPANSION_BGM_CONTEXT_WORLD_MAP,
    EXPANSION_BGM_CONTEXT_EVENT,
    EXPANSION_BGM_CONTEXT_SUPPORT,
    EXPANSION_BGM_CONTEXT_SHOP,
    EXPANSION_BGM_CONTEXT_STAFF,
    EXPANSION_BGM_CONTEXT_DANCE,
    EXPANSION_BGM_CONTEXT_TITLE,
    EXPANSION_BGM_CONTEXT_VICTORY,
    EXPANSION_BGM_CONTEXT_GAME_OVER,
    EXPANSION_BGM_CONTEXT_COUNT,
};

enum ExpansionBgmAction
{
    EXPANSION_BGM_ACTION_DANCE = 0,
    EXPANSION_BGM_ACTION_STAFF = 1,
    EXPANSION_BGM_ACTION_COUNT,
};

enum ExpansionBgmStaffKind
{
    EXPANSION_BGM_STAFF_NONE = 0,
    EXPANSION_BGM_STAFF_HEAL = 1,
    EXPANSION_BGM_STAFF_CURE = 2,
};

enum ExpansionBgmContinuationPolicy
{
    /* Keep any currently active song; do not resolve or restart it. */
    EXPANSION_BGM_CONTINUATION_PRESERVE = 0,
    /* Resolve the requested context and restore it when needed. */
    EXPANSION_BGM_CONTINUATION_RESUME = 1,
    /* Resolve the requested context and forcibly fade it in again. */
    EXPANSION_BGM_CONTINUATION_RESTART = 2,
};

#ifndef FE8_EXPANSION_BGM_CONTINUATION_POLICY
#define FE8_EXPANSION_BGM_CONTINUATION_POLICY EXPANSION_BGM_CONTINUATION_PRESERVE
#endif

#if (FE8_EXPANSION_BGM_CONTINUATION_POLICY < EXPANSION_BGM_CONTINUATION_PRESERVE) \
    || (FE8_EXPANSION_BGM_CONTINUATION_POLICY > EXPANSION_BGM_CONTINUATION_RESTART)
#error "FE8_EXPANSION_BGM_CONTINUATION_POLICY must be preserve (0), resume (1), or restart (2)"
#endif

#define EXPANSION_BGM_VARIANT_MATCH_CHAPTER (1 << 0)
#define EXPANSION_BGM_VARIANT_MATCH_FLAG (1 << 1)
#define EXPANSION_BGM_SELECTOR_MATCH_STAFF_KIND (1 << 0)
#define EXPANSION_BGM_SELECTOR_MATCH_CHARACTER (1 << 1)
#define EXPANSION_BGM_SELECTOR_MATCH_CLASS (1 << 2)
#define EXPANSION_BGM_SELECTOR_MATCH_ITEM (1 << 3)

struct ExpansionBgmContextRequest
{
    /* 00 */ enum ExpansionBgmContext context;
    /* 04 */ u16 chapterId;
    /* 06 */ u8 hasExplicitSong;
    /* 07 */ u8 padding;
    /* 08 */ int fallbackSong;
    /* 0C */ int explicitSong;
};

struct ExpansionBgmVariant
{
    /* 00 */ u16 chapterId;
    /* 02 */ u16 flagId;
    /* 04 */ u16 songId;
    /* 06 */ u8 context;
    /* 07 */ u8 whenFlagSet;
    /* 08 */ u8 priority;
    /* 09 */ u8 matchMask;
    /* 0A */ u8 padding[2];
};

struct ExpansionBgmActionSelector
{
    /* 00 */ u8 action;
    /* 01 */ u8 priority;
    /* 02 */ u8 staffKind;
    /* 03 */ u8 characterId;
    /* 04 */ u8 classId;
    /* 05 */ u8 matchMask;
    /* 06 */ u16 itemId;
    /* 08 */ u16 songId;
};

extern const struct ExpansionBgmVariant gExpansionBgmVariants[];
extern const u32 gExpansionBgmVariantCount;
extern const struct ExpansionBgmActionSelector gExpansionBgmActionSelectors[];
extern const u32 gExpansionBgmActionSelectorCount;

int ExpansionBgm_Resolve(const struct ExpansionBgmContextRequest *request);
int ExpansionBgm_SelectActionSong(
    enum ExpansionBgmAction action,
    const struct Unit *unit,
    int item,
    enum ExpansionBgmStaffKind staffKind,
    int legacySong);

void ExpansionBgm_Start(
    enum ExpansionBgmContext context,
    int fallbackSong,
    int speed,
    struct MusicPlayerInfo *player);
void ExpansionBgm_StartExplicit(
    enum ExpansionBgmContext context,
    int songId,
    int speed,
    struct MusicPlayerInfo *player);
void ExpansionBgm_FadeInExplicit(
    enum ExpansionBgmContext context,
    int songId,
    int duration,
    struct MusicPlayerInfo *player);
void ExpansionBgm_Change(
    enum ExpansionBgmContext context,
    int fallbackSong,
    int volumeInit,
    int volumeEnd,
    int duration,
    ProcPtr parent);
void ExpansionBgm_ChangeExplicit(
    enum ExpansionBgmContext context,
    int songId,
    int volumeInit,
    int volumeEnd,
    int duration,
    ProcPtr parent);
void ExpansionBgm_Override(enum ExpansionBgmContext context, int songId);
void ExpansionBgm_Restore(enum ExpansionBgmContext context, u16 speed);
void ExpansionBgm_Continue(
    enum ExpansionBgmContext context,
    int songId,
    int speed,
    struct MusicPlayerInfo *player);
int ExpansionBgm_GetContinuationPolicy(void);

#endif /* GUARD_EXPANSION_BGM_H */
