#ifndef GUARD_GRADO_CAMPAIGN_STATE_H
#define GUARD_GRADO_CAMPAIGN_STATE_H

#include "grado_state.h"

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

enum GradoAllocationResult GradoState_ValidateCampaign(void);

enum GradoStateValueResult GradoState_ReadSymbol(
    enum GradoStateSymbol symbol, unsigned int chapter, u16 * value);

enum GradoStateValueResult GradoState_WriteSymbol(
    enum GradoStateSymbol symbol, unsigned int chapter, u16 value);

enum GradoStateValueResult GradoState_ReadRescue(enum GradoRescueFact fact, u16 * value);
enum GradoStateValueResult GradoState_RecordRescue(enum GradoRescueFact fact);

#endif
