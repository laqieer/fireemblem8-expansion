#ifndef GUARD_GRADO_STATE_H
#define GUARD_GRADO_STATE_H

#include "global.h"

enum GradoStateFieldKind
{
    GRADO_STATE_BOOLEAN,
    GRADO_STATE_ONE_HOT,
    GRADO_STATE_COUNTER,
    GRADO_STATE_MONOTONIC,
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

enum GradoStateLoadRoute
{
    GRADO_LOAD_NEW_GAME,
    GRADO_LOAD_NEW_CHAPTER,
    GRADO_LOAD_NORMAL,
    GRADO_LOAD_SUSPEND,
    GRADO_LOAD_LEGACY,
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
    GRADO_STATE_VALUE_MONOTONIC,
    GRADO_STATE_VALUE_READ_ONLY,
    GRADO_STATE_VALUE_CONFLICT,
};

enum GradoStateRecovery
{
    GRADO_STATE_RECOVERY_NONE,
    GRADO_STATE_RECOVERY_INITIALIZE_GUIDE,
    GRADO_STATE_RECOVERY_RODERICH_EXILED,
    GRADO_STATE_RECOVERY_REOPEN_DIRECTION,
    GRADO_STATE_RECOVERY_CHOICE,
    GRADO_STATE_RECOVERY_LUND_ABANDONED,
};

enum GradoStateLoadResult
{
    GRADO_STATE_LOAD_VALID,
    GRADO_STATE_LOAD_INPUT,
    GRADO_STATE_LOAD_INCOMPATIBLE,
    GRADO_STATE_LOAD_CORRUPT,
    GRADO_STATE_LOAD_MISSING,
    GRADO_STATE_LOAD_CONFLICT,
    GRADO_STATE_LOAD_RECOVERY,
    GRADO_STATE_LOAD_REOPEN,
};

enum
{
    GRADO_STATE_CHAPTER_COUNT = 22,
    GRADO_STATE_NO_CHAPTER = 0xFF,
    GRADO_STATE_PERMANENT_BYTES = 25,
    GRADO_STATE_CHAPTER_BYTES = 5,
    GRADO_STATE_MAX_RECOVERIES = 14,
    GRADO_STATE_NO_NATIVE_SAVE_SLOT = 0xFF,
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

struct GradoStateFlagView
{
    const void * chapterFlags;
    unsigned int chapterBytes;
    const void * permanentFlags;
    unsigned int permanentBytes;
};

struct GradoStateLoadDiagnostic
{
    enum GradoStateLoadResult result;
    enum GradoStateRecovery recovery;
    u16 missingSymbol;
    u16 producerSymbol;
    enum GradoStateLoadRoute route;
    unsigned int chapter;
    u8 preflightPermanent[GRADO_STATE_PERMANENT_BYTES];
    u8 preflightChapter[GRADO_STATE_CHAPTER_BYTES];
    u16 recoveredSymbols[GRADO_STATE_MAX_RECOVERIES];
    u8 recoveredCount;
    u8 guideTruthInitialized;
    u8 sourceSlot;
    u8 backingSlot;
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

enum GradoStateValueResult GradoState_ReadValueFrom(
    const struct GradoStateField * field,
    enum GradoGuideStoragePolicy guidePolicy,
    unsigned int chapter,
    const struct GradoStateFlagView * view,
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
