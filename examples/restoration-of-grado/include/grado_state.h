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

#endif
