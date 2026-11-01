#include "global.h"

#include "constants/event-flags.h"
#include "../include/grado_state.h"

int GradoState_IsProgressiveGuideFlag(unsigned int flag)
{
    return (flag >= 0xB4 && flag <= 0xEB) || (flag >= 0xF0 && flag <= 0x127);
}

static int GradoState_IsReservedFlag(unsigned int flag, unsigned int scope)
{
    if (scope == GRADO_STATE_CHAPTER)
        return flag <= EVFLAG_DEFEAT_ALL || flag == 39;

    return flag <= EVFLAG_EXTRA_UNIT(9) || (flag >= 0x71 && flag <= 0x77)
        || flag == 0x7D || flag == 0x82 || flag == EVFLAG_HIDE_BLINKING_ICON
        || flag == 0x87 || flag == EVFLAG_136 || flag == 0x89 || flag == 0x8A || flag == 0xB3;
}

enum GradoAllocationResult GradoState_ValidateAllocation(
    const struct GradoStateField * fields,
    unsigned int count,
    enum GradoGuideStoragePolicy guidePolicy)
{
    unsigned int i;
    unsigned int j;
    unsigned int flag;
    unsigned int end;
    const struct GradoStateField * field;
    const struct GradoStateField * previous;

    if (fields == 0 || count == 0
        || (guidePolicy != GRADO_GUIDE_PROGRESSIVE && guidePolicy != GRADO_GUIDE_STATIC))
        return GRADO_ALLOCATION_INPUT;

    for (i = 0; i < count; ++i)
    {
        field = fields + i;

        if ((field->scope == GRADO_STATE_CHAPTER && field->chapter >= GRADO_STATE_CHAPTER_COUNT)
            || (field->scope == GRADO_STATE_PERMANENT && field->chapter != GRADO_STATE_NO_CHAPTER)
            || (field->scope != GRADO_STATE_CHAPTER && field->scope != GRADO_STATE_PERMANENT))
            return GRADO_ALLOCATION_SCOPE;

        if (field->bitCount == 0 || field->bitCount > 8)
            return GRADO_ALLOCATION_VALUE;

        switch (field->kind)
        {
        case GRADO_STATE_BOOLEAN:
        case GRADO_STATE_MONOTONIC:
            if (field->bitCount != 1 || field->maxValue != 1)
                return GRADO_ALLOCATION_VALUE;
            if (field->kind == GRADO_STATE_MONOTONIC && field->scope != GRADO_STATE_PERMANENT)
                return GRADO_ALLOCATION_SCOPE;
            break;

        case GRADO_STATE_ONE_HOT:
            if (field->bitCount < 2 || field->maxValue != field->bitCount)
                return GRADO_ALLOCATION_VALUE;
            break;

        case GRADO_STATE_COUNTER:
            if (field->maxValue == 0 || field->maxValue >= (1u << field->bitCount))
                return GRADO_ALLOCATION_VALUE;
            break;

        default:
            return GRADO_ALLOCATION_VALUE;
        }

        end = field->firstFlag + field->bitCount;

        if ((field->scope == GRADO_STATE_CHAPTER && (field->firstFlag == 0 || end > 41))
            || (field->scope == GRADO_STATE_PERMANENT && (field->firstFlag <= 100 || end > 301)))
            return GRADO_ALLOCATION_CAPACITY;

        for (flag = field->firstFlag; flag < end; ++flag)
        {
            if (GradoState_IsReservedFlag(flag, field->scope))
                return GRADO_ALLOCATION_RESERVED;

            if (field->scope == GRADO_STATE_PERMANENT
                && guidePolicy == GRADO_GUIDE_PROGRESSIVE
                && GradoState_IsProgressiveGuideFlag(flag))
                return GRADO_ALLOCATION_GUIDE_OWNED;
        }

        for (j = 0; j < i; ++j)
        {
            previous = fields + j;

            if (previous->scope != field->scope
                || (field->scope == GRADO_STATE_CHAPTER && previous->chapter != field->chapter))
                continue;

            if (field->firstFlag < previous->firstFlag + previous->bitCount
                && previous->firstFlag < end)
                return GRADO_ALLOCATION_OVERLAP;
        }
    }

    return GRADO_ALLOCATION_VALID;
}
