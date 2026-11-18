#include "global.h"

#include <string.h>

#include "eventinfo.h"
#include "../include/grado_campaign_state.h"
#include "../include/grado_state_internal.h"

struct GradoStateRequirement
{
    enum GradoStateSymbol value;
    enum GradoStateSymbol producer;
};

static const struct GradoStateRequirement sRequirements[] = {
    { GRADO_STATE_LEGITIMACY, GRADO_FLAG_CH02_CLEAR },
    { GRADO_STATE_RENAIS, GRADO_FLAG_CH06_CLEAR },
    { GRADO_FLAG_CH07_GUARD_KEPT, GRADO_FLAG_CH07_CLEAR },
    { GRADO_STATE_SALTROAD, GRADO_FLAG_CH08_CLEAR },
    { GRADO_FLAG_CH09_MEDNET, GRADO_FLAG_CH09_CLEAR },
    { GRADO_STATE_TRUTH, GRADO_FLAG_CH10_CLEAR },
    { GRADO_STATE_RODERICH, GRADO_FLAG_CH10_CLEAR },
    { GRADO_STATE_RATION, GRADO_FLAG_CH11_CLEAR },
    { GRADO_STATE_WYVERN, GRADO_FLAG_CH13_CLEAR },
    { GRADO_STATE_VILLAGE, GRADO_FLAG_CH14_CLEAR },
    { GRADO_STATE_RAUSTEN_MED, GRADO_FLAG_CH15_CLEAR },
    { GRADO_STATE_RAUSTEN_DATA, GRADO_FLAG_CH15_CLEAR },
    { GRADO_STATE_DIRECTION, GRADO_FLAG_CH18_CLEAR },
};

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

enum GradoStateLoadResult GradoState_PreflightSave(
    enum SaveCompatState compatibility,
    const struct ExpansionSaveMeta * meta,
    enum GradoStateLoadRoute route,
    unsigned int chapter,
    const struct GradoStateFlagView * view,
    struct GradoStateLoadDiagnostic * diagnostic)
{
    enum GradoStateLoadResult result;
    enum GradoStateValueResult valueResult;
    const struct GradoStateRequirement * requirement;
    unsigned int i;
    u16 producer;
    u16 value;

    if (diagnostic == NULL)
        return GRADO_STATE_LOAD_INPUT;

    if (route != GRADO_LOAD_NORMAL && route != GRADO_LOAD_SUSPEND
        && route != GRADO_LOAD_LEGACY)
    {
        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_INPUT,
            GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_SYMBOL_COUNT);
    }

    if (chapter >= GRADO_STATE_CHAPTER_COUNT || view == NULL
        || !GradoState_MetaMatches(compatibility, meta)
        || (route == GRADO_LOAD_SUSPEND
            && (view->chapterFlags == NULL
                || view->chapterBytes < (unsigned int)GetChapterFlagBitsSize())))
    {
        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_INCOMPATIBLE,
            GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_SYMBOL_COUNT);
    }

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

    value = 0;
    valueResult = GradoState_ReadGuideTruthFrom(view, &value);
    if (valueResult == GRADO_STATE_VALUE_UNSET)
        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_RECOVERY,
            GRADO_STATE_RECOVERY_INITIALIZE_GUIDE, GRADO_STATE_SYMBOL_COUNT,
            GRADO_STATE_SYMBOL_COUNT);
    if (valueResult != GRADO_STATE_VALUE_VALID)
        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_CORRUPT,
            GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT,
            GRADO_STATE_SYMBOL_COUNT);
    if (value == 0)
        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_RECOVERY,
            GRADO_STATE_RECOVERY_INITIALIZE_GUIDE, GRADO_STATE_SYMBOL_COUNT,
            GRADO_STATE_SYMBOL_COUNT);

    for (i = 0; i < sizeof(sRequirements) / sizeof(sRequirements[0]); ++i)
    {
        requirement = sRequirements + i;
        valueResult = GradoState_ReadSymbolFrom(
            requirement->producer, chapter, view, &producer);
        if (valueResult != GRADO_STATE_VALUE_VALID || producer == 0)
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

        if (requirement->value == GRADO_STATE_RODERICH)
            return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_RECOVERY,
                GRADO_STATE_RECOVERY_RODERICH_EXILED, requirement->value,
                requirement->producer);

        if (requirement->value == GRADO_STATE_DIRECTION)
            return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_REOPEN,
                GRADO_STATE_RECOVERY_REOPEN_DIRECTION, requirement->value,
                requirement->producer);

        return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_MISSING,
            GRADO_STATE_RECOVERY_NONE, requirement->value, requirement->producer);
    }

    return GradoState_SetLoadResult(diagnostic, GRADO_STATE_LOAD_VALID,
        GRADO_STATE_RECOVERY_NONE, GRADO_STATE_SYMBOL_COUNT, GRADO_STATE_SYMBOL_COUNT);
}

enum GradoStateLoadResult GradoState_ApplyLoadRecovery(
    const struct GradoStateLoadDiagnostic * diagnostic)
{
    enum GradoStateValueResult result;
    u16 value;

    if (diagnostic == NULL)
        return GRADO_STATE_LOAD_INPUT;

    if (diagnostic->result == GRADO_STATE_LOAD_REOPEN
        && diagnostic->recovery == GRADO_STATE_RECOVERY_REOPEN_DIRECTION)
        return GRADO_STATE_LOAD_REOPEN;

    if (diagnostic->result != GRADO_STATE_LOAD_RECOVERY)
        return GRADO_STATE_LOAD_INCOMPATIBLE;

    if (diagnostic->recovery == GRADO_STATE_RECOVERY_INITIALIZE_GUIDE)
    {
        const struct GradoStateFlagView view = {
            GetChapterFlagBits(), GetChapterFlagBitsSize(),
            GetPermanentFlagBits(), GetPermanentFlagBitsSize(),
        };

        result = GradoState_ReadGuideTruthFrom(&view, &value);
        if (result == GRADO_STATE_VALUE_VALID && value == 0)
        {
            SetFlag(GRADO_INTERNAL_GUIDE_TRUTH);
            return GRADO_STATE_LOAD_VALID;
        }
        if (result == GRADO_STATE_VALUE_UNSET)
        {
            SetFlag(GRADO_INTERNAL_GUIDE_TRUTH);
            return GRADO_STATE_LOAD_VALID;
        }
        return GRADO_STATE_LOAD_CONFLICT;
    }

    if (diagnostic->recovery == GRADO_STATE_RECOVERY_RODERICH_EXILED)
    {
        if (diagnostic->missingSymbol != GRADO_STATE_RODERICH
            || diagnostic->producerSymbol != GRADO_FLAG_CH10_CLEAR)
            return GRADO_STATE_LOAD_CONFLICT;
        result = GradoState_ReadSymbol(
            GRADO_FLAG_CH10_CLEAR, GRADO_STATE_NO_CHAPTER, &value);
        if (result != GRADO_STATE_VALUE_VALID || value != 1)
            return GRADO_STATE_LOAD_CONFLICT;
        result = GradoState_ReadSymbol(
            GRADO_STATE_RODERICH, GRADO_STATE_NO_CHAPTER, &value);
        if (result != GRADO_STATE_VALUE_UNSET)
            return GRADO_STATE_LOAD_CONFLICT;
        result = GradoState_WriteSymbol(
            GRADO_STATE_RODERICH, GRADO_STATE_NO_CHAPTER, GRADO_RODERICH_EXILED);
        return result == GRADO_STATE_VALUE_VALID
            ? GRADO_STATE_LOAD_VALID : GRADO_STATE_LOAD_CONFLICT;
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
