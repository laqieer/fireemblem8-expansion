#include "global.h"

#include "eventinfo.h"
#include "../include/grado_state.h"

static enum GradoStateValueResult GradoState_CheckValueAccess(
    const struct GradoStateField * field,
    enum GradoGuideStoragePolicy guidePolicy,
    unsigned int chapter)
{
    enum GradoAllocationResult allocation;

    allocation = GradoState_ValidateAllocation(field, 1, guidePolicy);
    if (allocation == GRADO_ALLOCATION_INPUT)
        return GRADO_STATE_VALUE_INPUT;
    if (allocation != GRADO_ALLOCATION_VALID)
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
    const struct GradoStateFlagView view = {
        GetChapterFlagBits(), GetChapterFlagBitsSize(),
        GetPermanentFlagBits(), GetPermanentFlagBitsSize(),
    };

    return GradoState_ReadValueFrom(field, guidePolicy, chapter, &view, value);
}

enum GradoStateValueResult GradoState_ReadValueFrom(
    const struct GradoStateField * field,
    enum GradoGuideStoragePolicy guidePolicy,
    unsigned int chapter,
    const struct GradoStateFlagView * view,
    u16 * value)
{
    enum GradoStateValueResult result;
    const void * flags;
    unsigned int bytes;
    unsigned int requiredBytes;
    unsigned int firstFlag;
    unsigned int raw;
    unsigned int bit;
    unsigned int decoded;

    if (value == NULL || view == NULL)
        return GRADO_STATE_VALUE_INPUT;

    result = GradoState_CheckValueAccess(field, guidePolicy, chapter);
    if (result != GRADO_STATE_VALUE_VALID)
        return result;

    firstFlag = field->firstFlag;
    if (field->scope == GRADO_STATE_CHAPTER)
    {
        flags = view->chapterFlags;
        bytes = view->chapterBytes;
        requiredBytes = GetChapterFlagBitsSize();
        /* Chapter flags use the same packed-bit layout as the native permanent reader. */
        firstFlag += 100;
    }
    else
    {
        flags = view->permanentFlags;
        bytes = view->permanentBytes;
        requiredBytes = GetPermanentFlagBitsSize();
    }
    if (flags == NULL || bytes < requiredBytes)
        return GRADO_STATE_VALUE_INPUT;

    raw = 0;
    /* The native packed reader's legacy buffer parameter is not const-qualified. */
    for (bit = 0; bit < field->bitCount; ++bit)
        if (CheckPermanentFlagFrom(firstFlag + bit, (void *)flags))
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

    if (field->kind == GRADO_STATE_MONOTONIC && value == 0 && CheckFlag(field->firstFlag))
        return GRADO_STATE_VALUE_MONOTONIC;

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
