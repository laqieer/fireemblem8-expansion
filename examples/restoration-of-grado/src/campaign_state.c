#include "global.h"

#include "eventinfo.h"
#include "../include/grado_campaign_state.h"

enum GradoCampaignField
{
#define GRADO_FIELD(symbol, first, bits, kind, maximum, chapter, scope) FIELD_##symbol,
#define GRADO_ALIAS(symbol, target, value)
#define GRADO_COUNT(symbol, group)
#define GRADO_ENDING(symbol, target)
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum) FIELD_##symbol,
#include "../include/grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_COUNT
#undef GRADO_ENDING
#undef GRADO_PRIVATE
    CAMPAIGN_FIELD_COUNT,
};

static const struct GradoStateField sCampaignFields[] = {
#define GRADO_FIELD(symbol, first, bits, kind, maximum, chapter, scope) \
    { first, bits, kind, maximum, chapter, scope },
#define GRADO_ALIAS(symbol, target, value)
#define GRADO_COUNT(symbol, group)
#define GRADO_ENDING(symbol, target)
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum) \
    GRADO_FIELD(symbol, first, bits, kind, maximum, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT)
#include "../include/grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_COUNT
#undef GRADO_ENDING
#undef GRADO_PRIVATE
};

enum GradoBindingKind
{
    BINDING_DIRECT,
    BINDING_ALIAS,
    BINDING_COUNT_CIVILIAN,
    BINDING_COUNT_ARCHIVE,
    BINDING_ENDING,
};

struct GradoStateBinding
{
    u16 field;
    u8 kind;
    u8 value;
};

static const struct GradoStateBinding sCampaignBindings[] = {
#define GRADO_FIELD(symbol, first, bits, kind, maximum, chapter, scope) \
    { FIELD_##symbol, BINDING_DIRECT, 0 },
#define GRADO_ALIAS(symbol, target, value) { FIELD_##target, BINDING_ALIAS, value },
#define GRADO_COUNT(symbol, group) { 0, BINDING_COUNT_##group, 0 },
#define GRADO_ENDING(symbol, target) { FIELD_##target, BINDING_ENDING, 0 },
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum)
#include "../include/grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_COUNT
#undef GRADO_ENDING
#undef GRADO_PRIVATE
};

static const enum GradoCampaignField sRescueFields[] = {
    FIELD_GRADO_FACT_CIV_01,
    FIELD_GRADO_FACT_CIV_02,
    FIELD_GRADO_FACT_CIV_03,
    FIELD_GRADO_FACT_CIV_04,
    FIELD_GRADO_FACT_MILITARY_ORDERS,
    FIELD_GRADO_FACT_CIVILIAN_RELIEF,
    FIELD_GRADO_FACT_TREASURY_LOGISTICS,
    FIELD_GRADO_FACT_LYON_RESEARCH,
};

enum GradoAllocationResult GradoState_ValidateCampaign(void)
{
    enum GradoAllocationResult result;
    const struct GradoStateBinding * binding;
    const struct GradoStateField * field;
    unsigned int i;

    result = GradoState_ValidateAllocation(
        sCampaignFields, CAMPAIGN_FIELD_COUNT, GRADO_GUIDE_STATIC);
    if (result != GRADO_ALLOCATION_VALID)
        return result;

    for (i = 0; i < GRADO_STATE_SYMBOL_COUNT; ++i)
    {
        binding = sCampaignBindings + i;
        if (binding->field >= CAMPAIGN_FIELD_COUNT || binding->kind > BINDING_ENDING)
            return GRADO_ALLOCATION_INPUT;

        field = sCampaignFields + binding->field;
        if (binding->kind == BINDING_ALIAS
            && (field->kind != GRADO_STATE_ONE_HOT
                || binding->value == 0 || binding->value > field->maxValue))
            return GRADO_ALLOCATION_VALUE;
    }

    return GRADO_ALLOCATION_VALID;
}

enum GradoStateValueResult GradoState_ReadGuideTruthFrom(
    const struct GradoStateFlagView * view, u16 * value)
{
    if (view == NULL || value == NULL)
        return GRADO_STATE_VALUE_INPUT;

    return GradoState_ReadValueFrom(
        sCampaignFields + FIELD_GRADO_INTERNAL_GUIDE_TRUTH,
        GRADO_GUIDE_STATIC, GRADO_STATE_NO_CHAPTER, view, value);
}

enum GradoStateValueResult GradoState_ValidateCampaignView(
    enum GradoStateLoadRoute route,
    unsigned int chapter,
    const struct GradoStateFlagView * view)
{
    enum GradoStateValueResult result;
    unsigned int i;
    u16 value;

    if (view == NULL || (route != GRADO_LOAD_NORMAL && route != GRADO_LOAD_SUSPEND
        && route != GRADO_LOAD_LEGACY))
        return GRADO_STATE_VALUE_INPUT;

    for (i = 0; i < CAMPAIGN_FIELD_COUNT; ++i)
    {
        if (sCampaignFields[i].scope == GRADO_STATE_CHAPTER)
        {
            if (route != GRADO_LOAD_SUSPEND || sCampaignFields[i].chapter != chapter)
                continue;
        }

        result = GradoState_ReadValueFrom(
            sCampaignFields + i, GRADO_GUIDE_STATIC, chapter, view, &value);
        if (result != GRADO_STATE_VALUE_VALID && result != GRADO_STATE_VALUE_UNSET)
            return result == GRADO_STATE_VALUE_CORRUPT
                && sCampaignFields[i].kind == GRADO_STATE_ONE_HOT
                ? GRADO_STATE_VALUE_CONFLICT : result;
    }

    return GRADO_STATE_VALUE_VALID;
}

enum GradoStateValueResult GradoState_ReadRescue(enum GradoRescueFact fact, u16 * value)
{
    if ((unsigned int)fact >= GRADO_RESCUE_COUNT)
        return GRADO_STATE_VALUE_INPUT;

    return GradoState_ReadValue(
        sCampaignFields + sRescueFields[fact], GRADO_GUIDE_STATIC, 0, value);
}

enum GradoStateValueResult GradoState_RecordRescue(enum GradoRescueFact fact)
{
    if ((unsigned int)fact >= GRADO_RESCUE_COUNT)
        return GRADO_STATE_VALUE_INPUT;

    return GradoState_WriteValue(
        sCampaignFields + sRescueFields[fact], GRADO_GUIDE_STATIC, 0, 1);
}

enum GradoStateValueResult GradoState_ReadSymbol(
    enum GradoStateSymbol symbol, unsigned int chapter, u16 * value)
{
    const struct GradoStateFlagView view = {
        GetChapterFlagBits(), GetChapterFlagBitsSize(),
        GetPermanentFlagBits(), GetPermanentFlagBitsSize(),
    };

    return GradoState_ReadSymbolFrom(symbol, chapter, &view, value);
}

enum GradoStateValueResult GradoState_ReadSymbolFrom(
    enum GradoStateSymbol symbol,
    unsigned int chapter,
    const struct GradoStateFlagView * view,
    u16 * value)
{
    const struct GradoStateBinding * binding;
    enum GradoStateValueResult result;
    unsigned int i;
    unsigned int first;
    u16 stored;
    u16 total;

    if ((unsigned int)symbol >= GRADO_STATE_SYMBOL_COUNT || value == 0)
        return GRADO_STATE_VALUE_INPUT;

    binding = sCampaignBindings + symbol;
    if (binding->kind == BINDING_COUNT_CIVILIAN || binding->kind == BINDING_COUNT_ARCHIVE)
    {
        first = binding->kind == BINDING_COUNT_CIVILIAN
            ? GRADO_RESCUE_CIV_01 : GRADO_RESCUE_MILITARY_ORDERS;
        total = 0;
        for (i = first; i < first + 4; ++i)
        {
            result = GradoState_ReadValueFrom(
                sCampaignFields + sRescueFields[i], GRADO_GUIDE_STATIC, 0, view, &stored);
            if (result != GRADO_STATE_VALUE_VALID)
                return result;
            total += stored;
        }
        *value = total;
        return GRADO_STATE_VALUE_VALID;
    }

    result = GradoState_ReadValueFrom(
        sCampaignFields + binding->field, GRADO_GUIDE_STATIC, chapter, view, &stored);
    if (binding->kind == BINDING_ALIAS && result == GRADO_STATE_VALUE_UNSET)
    {
        *value = 0;
        return GRADO_STATE_VALUE_VALID;
    }
    if (result != GRADO_STATE_VALUE_VALID)
        return result;

    *value = binding->kind == BINDING_ALIAS ? stored == binding->value : stored;
    return GRADO_STATE_VALUE_VALID;
}

enum GradoStateValueResult GradoState_WriteSymbol(
    enum GradoStateSymbol symbol, unsigned int chapter, u16 value)
{
    const struct GradoStateBinding * binding;

    if ((unsigned int)symbol >= GRADO_STATE_SYMBOL_COUNT)
        return GRADO_STATE_VALUE_INPUT;

    binding = sCampaignBindings + symbol;
    if (binding->kind != BINDING_DIRECT)
        return GRADO_STATE_VALUE_READ_ONLY;

    return GradoState_WriteValue(
        sCampaignFields + binding->field, GRADO_GUIDE_STATIC, chapter, value);
}
