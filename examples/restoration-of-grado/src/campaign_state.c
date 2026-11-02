#include "global.h"

#include "../include/grado_campaign_state.h"

enum GradoCampaignField
{
#define GRADO_FIELD(symbol, first, bits, kind, maximum, chapter, scope) FIELD_##symbol,
#define GRADO_ALIAS(symbol, target, value)
#define GRADO_DERIVED(symbol, kind, target)
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum) FIELD_##symbol,
#include "../include/grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_DERIVED
#undef GRADO_PRIVATE
    CAMPAIGN_FIELD_COUNT,
};

static const struct GradoStateField sCampaignFields[] = {
#define GRADO_FIELD(symbol, first, bits, kind, maximum, chapter, scope) \
    { first, bits, kind, maximum, chapter, scope },
#define GRADO_ALIAS(symbol, target, value)
#define GRADO_DERIVED(symbol, kind, target)
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum) \
    GRADO_FIELD(symbol, first, bits, kind, maximum, GRADO_STATE_NO_CHAPTER, GRADO_STATE_PERMANENT)
#include "../include/grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_DERIVED
#undef GRADO_PRIVATE
};

enum GradoBindingKind
{
    BINDING_DIRECT,
    BINDING_ALIAS,
    BINDING_CIVILIAN_COUNT,
    BINDING_ARCHIVE_COUNT,
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
#define GRADO_DERIVED(symbol, kind, target) { FIELD_##target, BINDING_##kind, 0 },
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum)
#include "../include/grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_DERIVED
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
    const struct GradoStateBinding * binding;
    enum GradoStateValueResult result;
    unsigned int i;
    unsigned int first;
    u16 stored;
    u16 total;

    if ((unsigned int)symbol >= GRADO_STATE_SYMBOL_COUNT || value == 0)
        return GRADO_STATE_VALUE_INPUT;

    binding = sCampaignBindings + symbol;
    if (binding->kind == BINDING_CIVILIAN_COUNT || binding->kind == BINDING_ARCHIVE_COUNT)
    {
        first = binding->kind == BINDING_CIVILIAN_COUNT
            ? GRADO_RESCUE_CIV_01 : GRADO_RESCUE_MILITARY_ORDERS;
        total = 0;
        for (i = first; i < first + 4; ++i)
        {
            result = GradoState_ReadRescue(i, &stored);
            if (result != GRADO_STATE_VALUE_VALID)
                return result;
            total += stored;
        }
        *value = total;
        return GRADO_STATE_VALUE_VALID;
    }

    result = GradoState_ReadValue(
        sCampaignFields + binding->field, GRADO_GUIDE_STATIC, chapter, &stored);
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
