#ifndef GUARD_GRADO_STATIC_GUIDE_EVENTS_H
#define GUARD_GRADO_STATIC_GUIDE_EVENTS_H

#include "EAstdlib.h"
#include "grado_guide_flag_ownership.h"

/* Use only while compiling inherited native event providers. */
#undef ENUT
#define ENUT(flag) \
    (GRADO_STATE_IS_PROGRESSIVE_GUIDE_FLAG(flag) \
        ? _EvtAutoCmdLen2(EV_CMD_NOP) \
        : _EvtArg0(EV_CMD_EVSET, 2, EVSUBCMD_ENUT, (flag))),

#endif
