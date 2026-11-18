#include "global.h"

#include <string.h>

#include "eventinfo.h"
#include "../include/grado_campaign_state.h"
#include "../include/grado_state_internal.h"

struct GradoStateRequirement
{
    enum GradoStateSymbol value;
    enum GradoStateSymbol producer;
    u16 recoveryValue;
    enum GradoStateSymbol absence;
    enum GradoStateRecovery recovery;
};

static const struct GradoStateRequirement sRequirements[] = {
    { GRADO_STATE_LEGITIMACY, GRADO_FLAG_CH02_CLEAR, 2,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_STATE_RENAIS, GRADO_FLAG_CH06_CLEAR, 2,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_FLAG_CH07_GUARD_KEPT, GRADO_FLAG_CH07_CLEAR, 0,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_NONE },
    { GRADO_STATE_SALTROAD, GRADO_FLAG_CH08_CLEAR, 2,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_FLAG_CH09_MEDNET, GRADO_FLAG_CH09_CLEAR, 0,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_NONE },
    { GRADO_STATE_TRUTH, GRADO_FLAG_CH10_CLEAR, 2,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_STATE_RATION, GRADO_FLAG_CH11_CLEAR, 3,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_STATE_WYVERN, GRADO_FLAG_CH13_CLEAR, 3,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_STATE_VILLAGE, GRADO_FLAG_CH14_CLEAR, 3,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_STATE_RAUSTEN_MED, GRADO_FLAG_CH15_CLEAR, 2,
        GRADO_FLAG_CH15_MED_NATASHA_ABSENT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_STATE_RAUSTEN_DATA, GRADO_FLAG_CH15_CLEAR, 2,
        GRADO_FLAG_CH15_DATA_KNOLL_ABSENT, GRADO_STATE_RECOVERY_CHOICE },
    { GRADO_STATE_RODERICH, GRADO_STATE_RODERICH, GRADO_RODERICH_EXILED,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_RODERICH_EXILED },
    { GRADO_STATE_LUND, GRADO_STATE_LUND, 3,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_LUND_ABANDONED },
    { GRADO_STATE_DIRECTION, GRADO_FLAG_CH18_CLEAR, 0,
        GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_RECOVERY_REOPEN_DIRECTION },
};

static enum GradoStateValueResult GradoState_ReadProducer(
    const struct GradoStateRequirement * requirement,
    const struct GradoStateFlagView * view, u16 * value)
{
    if (requirement->producer == requirement->value)
        return GradoState_ReadResolutionFrom(requirement->value, view, value);
    return GradoState_ReadSymbolFrom(
        requirement->producer, GRADO_STATE_NO_CHAPTER, view, value);
}

static enum GradoStateLoadResult GradoState_SetLoadResult(
    struct GradoStateLoadDiagnostic * diagnostic,
    enum GradoStateLoadResult result,
    enum GradoStateRecovery recovery,
    enum GradoStateSymbol missing,
    enum GradoStateSymbol producer)
{
    diagnostic->result = result;
    diagnostic->recovery = recovery;
    diagnostic->missingSymbol = missing;
    diagnostic->producerSymbol = producer;
    return result;
}

static int GradoState_MetaMatches(
    enum SaveCompatState compatibility,
    const struct ExpansionSaveMeta * meta)
{
    if (compatibility != SAVE_COMPAT_CURRENT || meta == NULL
        || meta->formatVersion != SAVE_FORMAT_VERSION_CURRENT
        || meta->compatEpoch != GRADO_SAVE_COMPAT_EPOCH
        || GRADO_SAVE_SCHEMA_VERSION != 1)
        return 0;

    return memcmp(meta->magic, EXPANSION_SAVE_META_MAGIC, EXPANSION_SAVE_META_MAGIC_SIZE) == 0;
}

static enum GradoStateLoadResult GradoState_CheckHistory(
    enum GradoStateLoadRoute route,
    unsigned int chapter,
    const struct GradoStateFlagView * view,
    struct GradoStateLoadDiagnostic * diagnostic)
{
    enum GradoStateLoadResult result;
    enum GradoStateValueResult valueResult;
    const struct GradoStateRequirement * requirement;
    struct GradoStateLoadDiagnostic pending;
    unsigned int i;
    u16 producer;
    u16 value;

    diagnostic->route = route;
    diagnostic->chapter = chapter;
    valueResult = GradoState_ValidateCampaignView(route, chapter, view);
    if (valueResult != GRADO_STATE_VALUE_VALID)
    {
        if (valueResult == GRADO_STATE_VALUE_INPUT)
            return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_INPUT,
                GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT,
                GRADO_STATE_SYMBOL_COUNT);
        result = valueResult == GRADO_STATE_VALUE_CONFLICT
            ? GRADO_STATE_LOAD_CONFLICT : GRADO_STATE_LOAD_CORRUPT;
        return GradoState_SetLoadResult(diagnostic, result, GRADO_STATE_RECOVERY_NONE,
            GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_SYMBOL_COUNT);
    }

    pending.route = route;
    pending.chapter = chapter;
    GradoState_SetLoadResult(&pending, GRADO_STATE_LOAD_VALID,
        GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_SYMBOL_COUNT);
    value = 0;
    valueResult = GradoState_ReadGuideTruthFrom(view, &value);
    if (valueResult != GRADO_STATE_VALUE_VALID && valueResult != GRADO_STATE_VALUE_UNSET)
        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_CORRUPT,
            GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT,
            GRADO_STATE_SYMBOL_COUNT);
    if (valueResult == GRADO_STATE_VALUE_UNSET || value == 0)
        GradoState_SetLoadResult(&pending, GRADO_STATE_LOAD_RECOVERY,
            GRADO_STATE_RECOVERY_INITIALIZE_GUIDE, GRADO_STATE_SYMBOL_COUNT,
            GRADO_STATE_SYMBOL_COUNT);

    for (i = 0; i < sizeof(sRequirements) / sizeof(sRequirements[0]); ++i)
    {
        requirement = sRequirements + i;
        valueResult = GradoState_ReadProducer(requirement, view, &producer);
        if (valueResult != GRADO_STATE_VALUE_VALID)
            return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_CORRUPT,
                GRADO_STATE_RECOVERY_NONE, requirement->value, requirement->producer);
        if (producer == 0)
            continue;

        valueResult = GradoState_ReadSymbolFrom(
            requirement->value, chapter, view, &value);
        if (valueResult == GRADO_STATE_VALUE_VALID)
            continue;
        if (valueResult == GRADO_STATE_VALUE_CORRUPT)
            return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_CONFLICT,
                GRADO_STATE_RECOVERY_NONE, requirement->value, requirement->producer);
        if (valueResult != GRADO_STATE_VALUE_UNSET)
            return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_CORRUPT,
                GRADO_STATE_RECOVERY_NONE, requirement->value, requirement->producer);

        if (requirement->value == GRADO_STATE_DIRECTION)
            return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_REOPEN,
                GRADO_STATE_RECOVERY_REOPEN_DIRECTION, requirement->value,
                requirement->producer);

        if (requirement->absence != GRADO_STATE_SYMBOL_COUNT)
        {
            valueResult = GradoState_ReadSymbolFrom(
                requirement->absence, chapter, view, &value);
            if (valueResult != GRADO_STATE_VALUE_VALID || value != 0)
                return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_MISSING,
                    GRADO_STATE_RECOVERY_NONE, requirement->value, requirement->producer);
        }

        if (requirement->recoveryValue != 0)
        {
            if (pending.result == GRADO_STATE_LOAD_VALID)
                GradoState_SetLoadResult(&pending, GRADO_STATE_LOAD_RECOVERY,
                    requirement->recovery, requirement->value, requirement->producer);
            continue;
        }

        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_MISSING,
            GRADO_STATE_RECOVERY_NONE, requirement->value, requirement->producer);
    }

    *diagnostic = pending;
    return pending.result;
}

enum GradoStateLoadResult GradoState_PreflightSave(
    enum SaveCompatState compatibility,
    const struct ExpansionSaveMeta * meta,
    enum GradoStateLoadRoute route,
    unsigned int chapter,
    const struct GradoStateFlagView * view,
    struct GradoStateLoadDiagnostic * diagnostic)
{
    if (diagnostic == NULL)
        return GRADO_STATE_LOAD_INPUT;
    diagnostic->route = route;
    diagnostic->chapter = chapter;
    if (route != GRADO_LOAD_NORMAL && route != GRADO_LOAD_SUSPEND
        && route != GRADO_LOAD_LEGACY)
        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_INPUT,
            GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_SYMBOL_COUNT);
    if (chapter >= GRADO_STATE_CHAPTER_COUNT || view == NULL
        || !GradoState_MetaMatches(compatibility, meta)
        || (route == GRADO_LOAD_SUSPEND
            && (view->chapterFlags == NULL
                || view->chapterBytes < (unsigned int)GetChapterFlagBitsSize())))
        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_INCOMPATIBLE,
            GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_SYMBOL_COUNT);
    return GradoState_CheckHistory(route, chapter, view, diagnostic);
}

enum GradoStateLoadResult GradoState_ApplyLoadRecovery(
    struct GradoStateLoadDiagnostic * diagnostic)
{
    const struct GradoStateFlagView view = {
        GetChapterFlagBits(), GetChapterFlagBitsSize(),
        GetPermanentFlagBits(), GetPermanentFlagBitsSize(),
    };
    const struct GradoStateRequirement * requirement;
    struct GradoStateLoadDiagnostic current;
    enum GradoStateValueResult result;
    unsigned int i;
    u16 value;

    if (diagnostic == NULL || diagnostic->chapter >= GRADO_STATE_CHAPTER_COUNT
        || (diagnostic->route != GRADO_LOAD_NORMAL && diagnostic->route != GRADO_LOAD_SUSPEND
            && diagnostic->route != GRADO_LOAD_LEGACY))
        return GRADO_STATE_LOAD_INPUT;

    if (diagnostic->result != GRADO_STATE_LOAD_RECOVERY)
        return GRADO_STATE_LOAD_INCOMPATIBLE;

    GradoState_CheckHistory(diagnostic->route, diagnostic->chapter, &view, &current);
    if (current.result != diagnostic->result || current.recovery != diagnostic->recovery
        || current.missingSymbol != diagnostic->missingSymbol
        || current.producerSymbol != diagnostic->producerSymbol)
        return GRADO_STATE_LOAD_CONFLICT;

    if (diagnostic->recovery == GRADO_STATE_RECOVERY_INITIALIZE_GUIDE)
    {
        result = GradoState_ReadGuideTruthFrom(&view, &value);
        if (result == GRADO_STATE_VALUE_VALID && value == 0)
        {
            SetFlag(GRADO_INTERNAL_GUIDE_TRUTH);
            return GradoState_CheckHistory(diagnostic->route, diagnostic->chapter, &view, diagnostic);
        }
        if (result == GRADO_STATE_VALUE_UNSET)
        {
            SetFlag(GRADO_INTERNAL_GUIDE_TRUTH);
            return GradoState_CheckHistory(diagnostic->route, diagnostic->chapter, &view, diagnostic);
        }
        return GRADO_STATE_LOAD_CONFLICT;
    }

    for (i = 0; i < sizeof(sRequirements) / sizeof(sRequirements[0]); ++i)
    {
        requirement = sRequirements + i;
        if (diagnostic->missingSymbol != requirement->value
            || diagnostic->producerSymbol != requirement->producer
            || diagnostic->recovery != requirement->recovery
            || requirement->recoveryValue == 0)
            continue;
        result = GradoState_ReadProducer(requirement, &view, &value);
        if (result != GRADO_STATE_VALUE_VALID || value != 1)
            return GRADO_STATE_LOAD_CONFLICT;
        result = GradoState_ReadSymbol(
            requirement->value, GRADO_STATE_NO_CHAPTER, &value);
        if (result != GRADO_STATE_VALUE_UNSET)
            return GRADO_STATE_LOAD_CONFLICT;
        if (requirement->absence != GRADO_STATE_SYMBOL_COUNT)
        {
            result = GradoState_ReadSymbol(
                requirement->absence, GRADO_STATE_NO_CHAPTER, &value);
            if (result != GRADO_STATE_VALUE_VALID || value != 0)
                return GRADO_STATE_LOAD_CONFLICT;
        }
        result = GradoState_WriteSymbol(
            requirement->value, GRADO_STATE_NO_CHAPTER, requirement->recoveryValue);
        if (result != GRADO_STATE_VALUE_VALID)
            return GRADO_STATE_LOAD_CONFLICT;
        return GradoState_CheckHistory(diagnostic->route, diagnostic->chapter, &view, diagnostic);
    }

    return GRADO_STATE_LOAD_INCOMPATIBLE;
}

enum GradoStateLoadResult GradoState_ResolveEnding(
    unsigned int chapter, u16 * ending)
{
    enum GradoStateValueResult result;

    if (ending == NULL)
        return GRADO_STATE_LOAD_INPUT;

    result = GradoState_ReadSymbol(GRADO_FLAG_FINAL_ENDING, chapter, ending);
    if (result == GRADO_STATE_VALUE_UNSET)
        return GRADO_STATE_LOAD_REOPEN;
    return result == GRADO_STATE_VALUE_VALID
        ? GRADO_STATE_LOAD_VALID : GRADO_STATE_LOAD_CONFLICT;
}

enum GradoStateLoadResult GradoState_BeginLifecycle(
    enum GradoStateLoadRoute route)
{
    switch (route)
    {
    case GRADO_LOAD_NEW_GAME:
        ResetPermanentFlags();
        ResetChapterFlags();
        SetFlag(GRADO_INTERNAL_GUIDE_TRUTH);
        return GRADO_STATE_LOAD_VALID;

    case GRADO_LOAD_NEW_CHAPTER:
        ResetChapterFlags();
        return GRADO_STATE_LOAD_VALID;

    case GRADO_LOAD_SUSPEND:
    case GRADO_LOAD_NORMAL:
    case GRADO_LOAD_LEGACY:
        return GRADO_STATE_LOAD_INCOMPATIBLE;

    default:
        return GRADO_STATE_LOAD_INPUT;
    }
}
