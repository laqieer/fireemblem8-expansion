/*
 * Host behavior driver for the production modern language-menu formatter
 * and synchronous startup initializer.
 */
#include <stdio.h>
#include <string.h>

#include "global.h"
#include "bmsave.h"
#include "expansion_language_menu.h"
#include "expansion_msg_ids.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "language-menu runtime host failure: %s\n", message); \
            return 0; \
        } \
    } while (0)

EWRAM_DATA u8 gSramBootFlags;
EWRAM_DATA u8 gLanguageMode;
EWRAM_DATA struct ExtraMapSaveHead gExtraMapSaveHead;

static int sApplySavedCalls;
static int sGetLangCalls;
static int sSetLangCalls;
static int sLoadCalls;
static int sNormalizeCalls;
static int sStoreCalls;
static ExpansionLocaleId sCurrentLocale;
static ExpansionLocaleId sResolvedLocale;
static ExpansionMsgId sResolvedMsg;

void ExpansionUiPrefs_ApplySaved(void)
{
    sApplySavedCalls++;
}

int GetLang(void)
{
    sGetLangCalls++;
    return gLanguageMode;
}

void SetLang(int language)
{
    sSetLangCalls++;
    gLanguageMode = (u8)language;
}

bool8 ExpansionLocale_IsSupported(ExpansionLocaleId locale)
{
    return locale < EXPANSION_LOCALE_COUNT;
}

bool8 ExpansionLocale_IsEnabled(ExpansionLocaleId locale)
{
    if (!ExpansionLocale_IsSupported(locale))
        return FALSE;

    return (FE8_EXPANSION_ENABLED_LOCALE_MASK & (1u << locale)) != 0;
}

ExpansionLocaleId ExpansionLocale_GetDefault(void)
{
    return (ExpansionLocaleId)FE8_EXPANSION_DEFAULT_LOCALE_ID;
}

ExpansionLocaleId ExpansionLocale_GetCurrent(void)
{
    return sCurrentLocale;
}

bool8 ExpansionLocale_SetCurrent(ExpansionLocaleId locale)
{
    if (!ExpansionLocale_IsEnabled(locale))
        return FALSE;

    sCurrentLocale = locale;
    return TRUE;
}

const char *ExpansionLocale_Resolve(ExpansionLocaleId locale, ExpansionMsgId msgId)
{
    static const char sResolved[] = "resolved";

    sResolvedLocale = locale;
    sResolvedMsg = msgId;
    return sResolved;
}

enum ExpansionUserPrefsState ExpansionUserPrefs_Load(struct ExpansionUserPrefs *out)
{
    sLoadCalls++;
    memset(out, 0, sizeof(*out));
    out->localeId = EXPANSION_LOCALE_IT;
    return EXPANSION_USER_PREFS_VALID;
}

enum ExpansionUserPrefsState ExpansionUserPrefs_Normalize(
    const struct ExpansionUserPrefs *prefs,
    enum ExpansionUserPrefsState state,
    ExpansionLocaleId *outLocaleId,
    bool8 *outRequiresPrompt)
{
    (void)prefs;
    sNormalizeCalls++;
    *outLocaleId = EXPANSION_LOCALE_IT;
    *outRequiresPrompt = FALSE;
    return state;
}

bool8 ExpansionUserPrefs_Store(ExpansionLocaleId locale, bool8 explicitSelection)
{
    (void)locale;
    (void)explicitSelection;
    sStoreCalls++;
    return TRUE;
}

void Proc_Goto(ProcPtr proc, int label)
{
    (void)proc;
    (void)label;
}

static int CheckFormatter(void)
{
    static const ExpansionMsgId sFullNameIds[EXPANSION_LOCALE_COUNT] =
    {
        EXP_MSG_FRAMEWORK_LOCALE_NAME_EN,
        EXP_MSG_FRAMEWORK_LOCALE_NAME_JA,
        EXP_MSG_FRAMEWORK_LOCALE_NAME_ZH_HANS,
        EXP_MSG_FRAMEWORK_LOCALE_NAME_FR,
        EXP_MSG_FRAMEWORK_LOCALE_NAME_DE,
        EXP_MSG_FRAMEWORK_LOCALE_NAME_ES,
        EXP_MSG_FRAMEWORK_LOCALE_NAME_IT,
        EXP_MSG_FRAMEWORK_LOCALE_NAME_QPS_PLOC,
    };
    static const ExpansionMsgId sShortNameIds[EXPANSION_LOCALE_COUNT] =
    {
        EXP_MSG_FRAMEWORK_LOCALE_SHORT_NAME_EN,
        EXP_MSG_FRAMEWORK_LOCALE_SHORT_NAME_JA,
        EXP_MSG_FRAMEWORK_LOCALE_SHORT_NAME_ZH_HANS,
        EXP_MSG_FRAMEWORK_LOCALE_SHORT_NAME_FR,
        EXP_MSG_FRAMEWORK_LOCALE_SHORT_NAME_DE,
        EXP_MSG_FRAMEWORK_LOCALE_SHORT_NAME_ES,
        EXP_MSG_FRAMEWORK_LOCALE_SHORT_NAME_IT,
        EXP_MSG_FRAMEWORK_LOCALE_SHORT_NAME_QPS_PLOC,
    };
    ExpansionLocaleId locale;

    for (locale = 0; locale < EXPANSION_LOCALE_COUNT; ++locale)
    {
        ExpansionLanguageMenu_ResolveLocaleName(locale, FALSE);
        CHECK(sResolvedLocale == EXPANSION_LOCALE_EN,
            "full locale names must resolve against English");
        CHECK(sResolvedMsg == sFullNameIds[locale],
            "full locale name used the wrong generated message id");

        ExpansionLanguageMenu_ResolveLocaleName(locale, TRUE);
        CHECK(sResolvedLocale == EXPANSION_LOCALE_EN,
            "compact locale names must resolve against English");
        CHECK(sResolvedMsg == sShortNameIds[locale],
            "compact locale name used the wrong generated message id");
    }

    ExpansionLanguageMenu_ResolveLocaleName(EXPANSION_LOCALE_INVALID, FALSE);
    CHECK(sResolvedMsg == EXP_MSG_FRAMEWORK_LOCALE_NAME_EN,
        "unsupported locale must use the configured default name");
    return 1;
}

static int CheckInitializer(u32 xmapMagic, u8 vanillaLanguage)
{
    struct ExtraMapSaveHead xmapBefore;

    memset(&gExpansionLanguageMenuProbe, 0, sizeof(gExpansionLanguageMenuProbe));
    memset(&gExtraMapSaveHead, 0xA5, sizeof(gExtraMapSaveHead));
    gExtraMapSaveHead.xmap_magic = xmapMagic;
    xmapBefore = gExtraMapSaveHead;
    gLanguageMode = vanillaLanguage;
    gSramBootFlags = SRAM_BOOT_FLAG_WRITES_ALLOWED;
    sApplySavedCalls = 0;
    sGetLangCalls = 0;
    sSetLangCalls = 0;
    sLoadCalls = 0;
    sNormalizeCalls = 0;
    sStoreCalls = 0;
    sCurrentLocale = EXPANSION_LOCALE_EN;

    ExpansionLanguageMenu_InitializeSingleLocaleBoot();

    CHECK(sApplySavedCalls == 1, "initializer must apply saved UI preferences once");
    CHECK(sLoadCalls == 1, "initializer must load expansion preferences once");
    CHECK(sNormalizeCalls == 1, "initializer must normalize expansion preferences once");
    CHECK(sStoreCalls == 0, "valid expansion preferences must not be rewritten");
    CHECK(sCurrentLocale == EXPANSION_LOCALE_IT,
        "valid expansion preference must become the current locale");
    CHECK(gExpansionLanguageMenuProbe.startupRunCount == 1,
        "initializer must record one startup run");
    CHECK(gExpansionLanguageMenuProbe.enabledLocaleCount == 5,
        "five-locale profile must record five enabled locales");
    CHECK(gExpansionLanguageMenuProbe.selectedLocale == EXPANSION_LOCALE_IT,
        "initializer probe must record the selected locale");
    CHECK(!gExpansionLanguageMenuProbe.promptShown,
        "valid preferences must not show the first-start selector");
    CHECK(sGetLangCalls == 0 && sSetLangCalls == 0,
        "initializer must not use vanilla language accessors");
    CHECK(gLanguageMode == vanillaLanguage,
        "initializer must not mutate vanilla language state");
    CHECK(memcmp(&gExtraMapSaveHead, &xmapBefore, sizeof(xmapBefore)) == 0,
        "initializer must not mutate XMAP state");
    return 1;
}

int main(void)
{
    if (!CheckFormatter())
        return 1;
    if (!CheckInitializer(XMAP_MAGIC, 1))
        return 1;
    if (!CheckInitializer(0xA5A5A5A5u, 7))
        return 1;
    return 0;
}
