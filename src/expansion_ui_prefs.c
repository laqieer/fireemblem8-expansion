#include "global.h"

#include "banim_presentation.h"
#include "expansion_config.h"
#include "expansion_save_prefs.h"
#include "expansion_ui_prefs.h"

static u8 sUtilityPrefs;
static bool8 sUtilityPrefsApplied;

static bool8 IsBattlePresentationAvailable(void)
{
    return TRUE;
}

static bool8 IsThreatRangeAvailable(void)
{
    return FE8_EXPANSION_DANGER_OVERLAY_MENU;
}

static u8 GetBattlePresentation(void)
{
    return BanimPresentationPolicy_GetCurrent()->id;
}

static bool8 SetBattlePresentation(u8 value)
{
    return BanimPresentationPolicy_Select(value);
}

static u8 GetThreatRange(void)
{
    return (sUtilityPrefs & EXPANSION_USER_PREFS_UTILITY_THREAT_RANGE) != 0;
}

static bool8 SetThreatRange(u8 value)
{
    if (!IsThreatRangeAvailable())
        return FALSE;

    if (value)
        sUtilityPrefs |= EXPANSION_USER_PREFS_UTILITY_THREAT_RANGE;
    else
        sUtilityPrefs &= (u8)~EXPANSION_USER_PREFS_UTILITY_THREAT_RANGE;

    return TRUE;
}

struct ExpansionUiPreferenceDescriptor const
    gExpansionUiPreferenceRegistry[EXPANSION_UI_PREF_COUNT] =
{
    [EXPANSION_UI_PREF_BATTLE_PRESENTATION] =
    {
        EXPANSION_UI_PREF_BATTLE_PRESENTATION,
        EXPANSION_USER_PREFS_DEFAULT_POLICY_ID,
        BANIM_PRESENTATION_POLICY_COUNT - 1,
        EXPANSION_UI_PREF_FLAG_RUNTIME,
        0, 0,
        IsBattlePresentationAvailable,
        GetBattlePresentation,
        SetBattlePresentation,
    },
    [EXPANSION_UI_PREF_THREAT_RANGE] =
    {
        EXPANSION_UI_PREF_THREAT_RANGE,
        0,
        1,
        EXPANSION_UI_PREF_FLAG_RUNTIME | EXPANSION_UI_PREF_FLAG_OPTIONAL,
        0, 0,
        IsThreatRangeAvailable,
        GetThreatRange,
        SetThreatRange,
    },
};

bool8 ExpansionUiPrefs_ValidateRegistry(void)
{
    u8 i;
    u8 j;

    for (i = 0; i < EXPANSION_UI_PREF_COUNT; ++i)
    {
        if (gExpansionUiPreferenceRegistry[i].id != i)
            return FALSE;
        if (gExpansionUiPreferenceRegistry[i].defaultValue > gExpansionUiPreferenceRegistry[i].maxValue)
            return FALSE;
        if (gExpansionUiPreferenceRegistry[i].isAvailable == NULL
            || gExpansionUiPreferenceRegistry[i].getValue == NULL
            || gExpansionUiPreferenceRegistry[i].setValue == NULL)
            return FALSE;

        for (j = 0; j < i; ++j)
        {
            if (gExpansionUiPreferenceRegistry[i].id == gExpansionUiPreferenceRegistry[j].id)
                return FALSE;
        }
    }

    return TRUE;
}

struct ExpansionUiPreferenceDescriptor const *ExpansionUiPrefs_Find(u8 id)
{
    if (id >= EXPANSION_UI_PREF_COUNT)
        return NULL;

    return &gExpansionUiPreferenceRegistry[id];
}

u8 ExpansionUiPrefs_Get(u8 id)
{
    struct ExpansionUiPreferenceDescriptor const *descriptor = ExpansionUiPrefs_Find(id);

    if (descriptor == NULL || !descriptor->isAvailable())
        return descriptor != NULL ? descriptor->defaultValue : 0;

    return descriptor->getValue();
}

bool8 ExpansionUiPrefs_Set(u8 id, u8 value)
{
    struct ExpansionUiPreferenceDescriptor const *descriptor = ExpansionUiPrefs_Find(id);

    if (descriptor == NULL || value > descriptor->maxValue || !descriptor->isAvailable())
        return FALSE;

    if (!descriptor->setValue(value))
        return FALSE;

    ExpansionUiPrefs_Persist();
    return TRUE;
}

void ExpansionUiPrefs_ApplySaved(void)
{
    struct ExpansionUserPrefs prefs;
    enum ExpansionUserPrefsState state;
    u8 policyId;
    u8 utilityFlags;

    state = ExpansionUserPrefs_Load(&prefs);
    if (state != EXPANSION_USER_PREFS_VALID && state != EXPANSION_USER_PREFS_MIGRATED)
    {
        BanimPresentationPolicy_AdoptAnimationOption(gPlaySt.config.animationType);
        sUtilityPrefs = 0;
        sUtilityPrefsApplied = TRUE;
        return;
    }

    if (prefs.reserved[2] != EXPANSION_USER_PREFS_VERSION_CURRENT)
    {
        BanimPresentationPolicy_AdoptAnimationOption(gPlaySt.config.animationType);
        sUtilityPrefs = 0;
        sUtilityPrefsApplied = TRUE;
        return;
    }

    ExpansionUserPrefs_GetSelections(&policyId, &utilityFlags);
    if (BanimPresentationPolicy_Get(policyId) == NULL)
        policyId = EXPANSION_USER_PREFS_DEFAULT_POLICY_ID;

    BanimPresentationPolicy_Select(policyId);
    sUtilityPrefs = utilityFlags & EXPANSION_USER_PREFS_UTILITY_MASK;
    sUtilityPrefsApplied = TRUE;
}

void ExpansionUiPrefs_Persist(void)
{
    if (!sUtilityPrefsApplied)
        return;

    ExpansionUserPrefs_StoreSelections(
        BanimPresentationPolicy_GetCurrent()->id,
        sUtilityPrefs);
}

void ExpansionUiPrefs_NotifyAnimationOptionChange(u8 animationOption)
{
    BanimPresentationPolicy_AdoptAnimationOption(animationOption);
    ExpansionUiPrefs_Persist();
}
