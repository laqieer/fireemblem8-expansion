#ifndef GUARD_GRADO_STATE_H
#define GUARD_GRADO_STATE_H

#include "global.h"

enum GradoStateFieldKind
{
    GRADO_STATE_BOOLEAN,
    GRADO_STATE_ONE_HOT,
    GRADO_STATE_COUNTER,
};

enum GradoStateScope
{
    GRADO_STATE_CHAPTER,
    GRADO_STATE_PERMANENT,
};

enum GradoGuideStoragePolicy
{
    GRADO_GUIDE_PROGRESSIVE,
    GRADO_GUIDE_STATIC,
};

enum GradoAllocationResult
{
    GRADO_ALLOCATION_VALID,
    GRADO_ALLOCATION_INPUT,
    GRADO_ALLOCATION_SCOPE,
    GRADO_ALLOCATION_VALUE,
    GRADO_ALLOCATION_CAPACITY,
    GRADO_ALLOCATION_RESERVED,
    GRADO_ALLOCATION_GUIDE_OWNED,
    GRADO_ALLOCATION_OVERLAP,
};

enum GradoStateValueResult
{
    GRADO_STATE_VALUE_VALID,
    GRADO_STATE_VALUE_UNSET,
    GRADO_STATE_VALUE_INPUT,
    GRADO_STATE_VALUE_LAYOUT,
    GRADO_STATE_VALUE_CHAPTER,
    GRADO_STATE_VALUE_CORRUPT,
    GRADO_STATE_VALUE_RANGE,
};

enum
{
    GRADO_STATE_CHAPTER_COUNT = 22,
    GRADO_STATE_NO_CHAPTER = 0xFF,
};

struct GradoStateField
{
    /* 00 */ u16 firstFlag;
    /* 02 */ u8 bitCount;
    /* 03 */ u8 kind;
    /* 04 */ u16 maxValue;
    /* 06 */ u8 chapter;
    /* 07 */ u8 scope;
};

enum GradoAllocationResult GradoState_ValidateAllocation(
    const struct GradoStateField * fields,
    unsigned int count,
    enum GradoGuideStoragePolicy guidePolicy);

enum GradoStateValueResult GradoState_ReadValue(
    const struct GradoStateField * field,
    enum GradoGuideStoragePolicy guidePolicy,
    unsigned int chapter,
    u16 * value);

enum GradoStateValueResult GradoState_WriteValue(
    const struct GradoStateField * field,
    enum GradoGuideStoragePolicy guidePolicy,
    unsigned int chapter,
    u16 value);

int GradoState_IsProgressiveGuideFlag(unsigned int flag);
int GradoGuide_CheckFlag(int flag);
void GradoGuide_SetFlag(int flag);
void GradoGuide_ClearFlag(int flag);

#endif
