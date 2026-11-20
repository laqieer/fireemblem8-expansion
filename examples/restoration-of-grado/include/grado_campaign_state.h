#ifndef GUARD_GRADO_CAMPAIGN_STATE_H
#define GUARD_GRADO_CAMPAIGN_STATE_H

#include "grado_state.h"
#include "save_format.h"

enum GradoStateSymbol
{
#define GRADO_FIELD(symbol, first, bits, kind, maximum, chapter, scope) symbol,
#define GRADO_ALIAS(symbol, target, value) symbol,
#define GRADO_COUNT(symbol, group) symbol,
#define GRADO_ENDING(symbol, target) symbol,
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum)
#include "grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_COUNT
#undef GRADO_ENDING
#undef GRADO_PRIVATE
    GRADO_STATE_SYMBOL_COUNT,
};

enum GradoRescueFact
{
    GRADO_RESCUE_CIV_01,
    GRADO_RESCUE_CIV_02,
    GRADO_RESCUE_CIV_03,
    GRADO_RESCUE_CIV_04,
    GRADO_RESCUE_MILITARY_ORDERS,
    GRADO_RESCUE_CIVILIAN_RELIEF,
    GRADO_RESCUE_TREASURY_LOGISTICS,
    GRADO_RESCUE_LYON_RESEARCH,
    GRADO_RESCUE_COUNT,
};

enum GradoRoderichState
{
    GRADO_RODERICH_UNSET,
    GRADO_RODERICH_KILLED,
    GRADO_RODERICH_CAPTURED,
    GRADO_RODERICH_EXILED,
};

enum
{
    GRADO_SAVE_SCHEMA_VERSION = 1,
    GRADO_SAVE_COMPAT_EPOCH = 256,
};

enum GradoAllocationResult GradoState_ValidateCampaign(void);

enum GradoStateValueResult GradoState_ValidateCampaignView(
    enum GradoStateLoadRoute route,
    unsigned int chapter,
    const struct GradoStateFlagView * view);

enum GradoStateValueResult GradoState_ReadGuideTruthFrom(
    const struct GradoStateFlagView * view, u16 * value);

enum GradoStateValueResult GradoState_ReadSymbol(
    enum GradoStateSymbol symbol, unsigned int chapter, u16 * value);

enum GradoStateValueResult GradoState_ReadSymbolFrom(
    enum GradoStateSymbol symbol,
    unsigned int chapter,
    const struct GradoStateFlagView * view,
    u16 * value);

enum GradoStateValueResult GradoState_WriteSymbol(
    enum GradoStateSymbol symbol, unsigned int chapter, u16 value);

enum GradoStateValueResult GradoState_ReadRescue(enum GradoRescueFact fact, u16 * value);
enum GradoStateValueResult GradoState_RecordRescue(enum GradoRescueFact fact);

enum GradoStateValueResult GradoState_ReadResolutionFrom(
    enum GradoStateSymbol symbol, const struct GradoStateFlagView * view, u16 * value);

enum GradoStateValueResult GradoState_RecordResolution(
    enum GradoStateSymbol symbol, unsigned int chapter);

enum GradoStateLoadResult GradoState_PreflightSave(
    enum SaveCompatState compatibility,
    const struct ExpansionSaveMeta * meta,
    enum GradoStateLoadRoute route,
    unsigned int chapter,
    const struct GradoStateFlagView * view,
    struct GradoStateLoadDiagnostic * diagnostic);

enum GradoStateLoadResult GradoState_PreflightNativeSave(
    enum GradoStateLoadRoute route,
    int slot,
    struct GradoStateLoadDiagnostic * diagnostic);

enum GradoStateLoadResult GradoState_LoadNativeSave(
    enum GradoStateLoadRoute route,
    int slot,
    struct GradoStateLoadDiagnostic * diagnostic);

int GradoState_TryLoadNativeSaveFromProc(
    enum GradoStateLoadRoute route, int slot, ProcPtr parentProc);
void GradoState_ReportNativeLoadRefusal(
    enum GradoStateLoadResult result, ProcPtr parentProc);

enum GradoStateLoadResult GradoState_ApplyLoadRecovery(
    struct GradoStateLoadDiagnostic * diagnostic);

enum GradoStateLoadResult GradoState_ResolveEnding(
    unsigned int chapter, u16 * ending);

enum GradoStateLoadResult GradoState_BeginLifecycle(
    enum GradoStateLoadRoute route);

#endif
