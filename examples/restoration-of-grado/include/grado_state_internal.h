#ifndef GUARD_GRADO_STATE_INTERNAL_H
#define GUARD_GRADO_STATE_INTERNAL_H

#include "grado_state.h"

enum GradoInternalFlag
{
#define GRADO_FIELD(symbol, first, bits, kind, maximum, chapter, scope)
#define GRADO_ALIAS(symbol, target, value)
#define GRADO_DERIVED(symbol, kind, target)
#define GRADO_PRIVATE(symbol, first, bits, kind, maximum) symbol = first,
#include "grado_campaign_state.def"
#undef GRADO_FIELD
#undef GRADO_ALIAS
#undef GRADO_DERIVED
#undef GRADO_PRIVATE
};

#endif
