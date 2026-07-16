#include "sample.h"

register int pinned asm("r4");
asm("" ::: "memory");
asm("swi 3");

NAKEDFUNC
void Naked(void) {}

__attribute__((naked))
void DirectNaked(void) {}

void Legacy();
void * callback = MISMATCHED_SIGNATURE(Legacy);

struct Packed {
    /* 00 */ unsigned value : 3;
    /* 04 */ unsigned other;
} BITPACKED;

CONST_DATA int forced_data[] = { 1 };
SHOULD_BE_CONST int mutable_for_matching;

#if MODERN
int modern_only;
#endif

const void * raw_pointer = (const void *) 0x08123456;
int fill_color = 0x08A708A7;
int layout_data[] = { 0x08001000 };
void Fill(void) { CpuFastFill(0x08A708A7, 0, 0x20); }
// Documentation only: 0x08ABCDEF
