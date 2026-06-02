#include "global.h"
#include "proc.h"
#include "efxbattle.h"

void sub_80707C0(struct Proc * proc);
void sub_80707FC(struct Proc * proc);

struct ProcCmd CONST_DATA ProcScr_EkrDragon_08758720[] =
{
    PROC_YIELD,
    PROC_CALL(sub_80707C0),
    PROC_REPEAT(sub_80707FC),

    PROC_END,
};
