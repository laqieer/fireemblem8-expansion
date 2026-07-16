#include "sample.h"

register int pinned asm("r4");
asm("" ::: "memory");
asm("swi 3");

NAKEDFUNC
void Naked(void) {}

void Legacy();
void * callback = MISMATCHED_SIGNATURE(Legacy);

struct Packed {
    /* 00 */ unsigned value : 3;
} BITPACKED;

CONST_DATA int forced_data[] = { 1 };
SHOULD_BE_CONST int mutable_for_matching;

#if MODERN
int modern_only;
#endif

const void * raw_pointer = (const void *) 0x08123456;
// Documentation only: 0x08ABCDEF
