#include "global.h"

#include "eventinfo.h"
#include "../include/grado_state.h"

static enum GradoStateValueResult GradoState_CheckValueAccess(
    const struct GradoStateField * field,
    enum GradoGuideStoragePolicy guidePolicy,
    unsigned int chapter)
{
    if (field == NULL)
        return GRADO_STATE_VALUE_INPUT;

    if (GradoState_ValidateAllocation(field, 1, guidePolicy) != GRADO_ALLOCATION_VALID)
        return GRADO_STATE_VALUE_LAYOUT;

    if (field->scope == GRADO_STATE_CHAPTER && field->chapter != chapter)
        return GRADO_STATE_VALUE_CHAPTER;

    return GRADO_STATE_VALUE_VALID;
}

enum GradoStateValueResult GradoState_ReadValue(
    const struct GradoStateField * field,
    enum GradoGuideStoragePolicy guidePolicy,
    unsigned int chapter,
    u16 * value)
{
    enum GradoStateValueResult result;
    unsigned int raw;
    unsigned int bit;
    unsigned int decoded;

    if (value == NULL)
        return GRADO_STATE_VALUE_INPUT;

    result = GradoState_CheckValueAccess(field, guidePolicy, chapter);
    if (result != GRADO_STATE_VALUE_VALID)
        return result;

    raw = 0;
    for (bit = 0; bit < field->bitCount; ++bit)
        if (CheckFlag(field->firstFlag + bit))
            raw |= 1u << bit;

    decoded = raw;
    if (field->kind == GRADO_STATE_ONE_HOT)
    {
        if (raw == 0)
            return GRADO_STATE_VALUE_UNSET;

        if (raw & (raw - 1))
            return GRADO_STATE_VALUE_CORRUPT;

        decoded = 1;
        while ((raw >>= 1) != 0)
            ++decoded;
    }

    if (decoded > field->maxValue)
        return GRADO_STATE_VALUE_CORRUPT;

    *value = decoded;
    return GRADO_STATE_VALUE_VALID;
}

enum GradoStateValueResult GradoState_WriteValue(
    const struct GradoStateField * field,
    enum GradoGuideStoragePolicy guidePolicy,
    unsigned int chapter,
    u16 value)
{
    enum GradoStateValueResult result;
    unsigned int raw;
    unsigned int bit;

    result = GradoState_CheckValueAccess(field, guidePolicy, chapter);
    if (result != GRADO_STATE_VALUE_VALID)
        return result;

    if (value > field->maxValue)
        return GRADO_STATE_VALUE_RANGE;

    raw = value;
    if (field->kind == GRADO_STATE_ONE_HOT)
        raw = value == 0 ? 0 : 1u << (value - 1);

    for (bit = 0; bit < field->bitCount; ++bit)
    {
        if (raw & (1u << bit))
            SetFlag(field->firstFlag + bit);
        else
            ClearFlag(field->firstFlag + bit);
    }

    return GRADO_STATE_VALUE_VALID;
}
