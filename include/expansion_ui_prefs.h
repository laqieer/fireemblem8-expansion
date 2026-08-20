#ifndef GUARD_EXPANSION_UI_PREFS_H
#define GUARD_EXPANSION_UI_PREFS_H

#include "global.h"

#define EXPANSION_UI_PREF_BATTLE_PRESENTATION 0
#define EXPANSION_UI_PREF_THREAT_RANGE 1
#define EXPANSION_UI_PREF_COUNT 2
#define EXPANSION_UI_PREF_INVALID 0xFF

#define EXPANSION_UI_PREF_FLAG_RUNTIME 0x01
#define EXPANSION_UI_PREF_FLAG_OPTIONAL 0x02

struct ExpansionUiPreferenceDescriptor
{
    /* 00 */ u8 id;
    /* 01 */ u8 defaultValue;
    /* 02 */ u8 maxValue;
    /* 03 */ u8 flags;
    /* 04 */ u16 labelMsgId;
    /* 06 */ u16 helpMsgId;
    /* 08 */ bool8 (*isAvailable)(void);
    /* 0C */ u8 (*getValue)(void);
    /* 10 */ bool8 (*setValue)(u8 value);
};

extern struct ExpansionUiPreferenceDescriptor const
    gExpansionUiPreferenceRegistry[EXPANSION_UI_PREF_COUNT];

bool8 ExpansionUiPrefs_ValidateRegistry(void);
struct ExpansionUiPreferenceDescriptor const *ExpansionUiPrefs_Find(u8 id);
u8 ExpansionUiPrefs_Get(u8 id);
bool8 ExpansionUiPrefs_Set(u8 id, u8 value);
void ExpansionUiPrefs_ApplySaved(void);
void ExpansionUiPrefs_Persist(void);
void ExpansionUiPrefs_NotifyAnimationOptionChange(u8 animationOption);

#endif
