#ifndef GUARD_EXPANSION_HQ_MIXER_H
#define GUARD_EXPANSION_HQ_MIXER_H

#include "global.h"

#if FE8_EXPANSION_HQ_MIXER

struct ExpansionHqMixerProbe
{
    /* 00 */ u32 initializationCount;
    /* 04 */ u32 sourceAddress;
    /* 08 */ u32 destinationAddress;
    /* 0C */ u32 codeBytes;
    /* 10 */ u32 sourceChecksum;
    /* 14 */ u32 destinationChecksum;
    /* 18 */ u32 mixBufferAddress;
    /* 1C */ u32 mixBufferBytes;
    /* 20 */ u32 dmaEnabled;
    /* 24 */ u32 soundMainCount;
    /* 28 */ u32 invalidDmaBufferCount;
};

extern struct ExpansionHqMixerProbe gExpansionHqMixerProbe;

#endif /* FE8_EXPANSION_HQ_MIXER */

#endif /* GUARD_EXPANSION_HQ_MIXER_H */
