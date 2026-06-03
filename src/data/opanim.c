#include "global.h"
#include "proc.h"
#include "opanim.h"

/* forward declarations not present in headers */
void OpAnimPreparefxEirika(struct Proc * proc);
void sub_80C7F90(struct Proc * proc);
void sub_80C8100(struct Proc * proc);
void sub_80C8184(struct Proc * proc);
void sub_80C8214(struct Proc * proc);
void sub_80C8278(struct Proc * proc);
void sub_80C835C(struct Proc * proc);
void sub_80C84D8(struct Proc * proc);
void sub_80C8564(struct Proc * proc);
void sub_80C8690(struct Proc * proc);
void sub_80C883C(struct Proc * proc);
void sub_80C8934(struct Proc * proc);
void sub_80C8A20(struct Proc * proc);
void sub_80C8B0C(struct Proc * proc);
void sub_80C8BF8(struct Proc * proc);
void sub_80C8CE4(struct Proc * proc);
void sub_80C8D30(struct Proc * proc);
void sub_80C8ED4(struct Proc * proc);
void sub_80C9024(struct Proc * proc);
void sub_80C9100(struct Proc * proc);
void sub_80C9218(struct Proc * proc);
void sub_80C9330(struct Proc * proc);
void sub_80C940C(struct Proc * proc);
void sub_80C955C(struct Proc * proc);
void sub_80C9638(struct Proc * proc);
void sub_80C9750(struct Proc * proc);
void sub_80C98A0(struct Proc * proc);
void sub_80C99B8(struct Proc * proc);
void sub_80C9A94(struct Proc * proc);
void sub_80C9AFC(struct Proc * proc);
void sub_80C9C08(struct Proc * proc);
void sub_80C9C5C(struct Proc * proc);
void sub_80C9CAC(struct Proc * proc);
void sub_80C9CFC(struct Proc * proc);
void sub_80C9D4C(struct Proc * proc);
void sub_80C9DA0(struct Proc * proc);
void sub_80C9DF0(struct Proc * proc);
void sub_80C9E6C(struct Proc * proc);
void sub_80C9EE8(struct Proc * proc);
void sub_80C9F7C(struct Proc * proc);
void sub_80C9FF8(struct Proc * proc);
void sub_80CA10C(struct Proc * proc);
void sub_80CA3B8(struct Proc * proc);
void sub_80CA4A4(struct Proc * proc);
void sub_80CA4DC(struct Proc * proc);
void sub_80CA92C(struct Proc * proc);
void sub_80CA940(struct Proc * proc);
void sub_80CAA38(struct Proc * proc);
void sub_80CABB0(struct Proc * proc);
void sub_80CAE20(struct Proc * proc);
void sub_80CAF2C(struct Proc * proc);
void sub_80CB0A0(struct Proc * proc);
void sub_80CB20C(struct Proc * proc);
void sub_80CB320(struct Proc * proc);
void sub_80CB594(struct Proc * proc);
void sub_80CB6A0(struct Proc * proc);
void sub_80CB878(struct Proc * proc);
void sub_80CBA64(struct Proc * proc);
void sub_80CBC40(struct Proc * proc);
void sub_80CBD7C(struct Proc * proc);
extern struct ProcCmd gUnkData_97[];

struct ProcCmd CONST_DATA ProcScr_OpAnim[] =
{
    PROC_YIELD,
    PROC_CALL(OpAnimInit),
    PROC_YIELD,
    PROC_CALL(SetupOpAnimWorldMapfx),
    PROC_START_CHILD(ProcScr_OpAnimBLDALPHA),
    PROC_WHILE(OpAnimBldAlphaExists),
    PROC_CALL(OpAnimUpdateScreen1),
    PROC_REPEAT(sub_80C6F70),
    PROC_CALL(sub_80C7050),
    PROC_YIELD,
    PROC_REPEAT(OpAnimPreparefxEphraim),
    PROC_CALL(NewProc08AA6D04),
    PROC_REPEAT(OpAnimEphraimfxFlyIn),
    PROC_REPEAT(sub_80C7900),
    PROC_REPEAT(OpAnim1AdvanceSplitLine),
    PROC_REPEAT(OpAnimEphraimMergeShadow),
    PROC_REPEAT(OpAnimEphraimDisplayName),
    PROC_REPEAT(OpAnimEphraimExit),
    PROC_REPEAT(OpAnimPreparefxEirika),
    PROC_REPEAT(sub_80C7F90),
    PROC_REPEAT(sub_80C8100),
    PROC_REPEAT(sub_80C8184),
    PROC_REPEAT(sub_80C8214),
    PROC_REPEAT(sub_80C8278),
    PROC_REPEAT(sub_80C835C),
    PROC_REPEAT(sub_80C84D8),
    PROC_CALL(sub_80C8564),
    PROC_START_CHILD(gUnkData_98),
    PROC_REPEAT(sub_80C8690),
    PROC_START_CHILD(gUnkData_97),
    PROC_CALL(sub_80C9DF0),
    PROC_REPEAT(sub_80C9E6C),
    PROC_CALL(sub_80C9C08),
    PROC_REPEAT(sub_80C9FF8),
    PROC_REPEAT(sub_80CA10C),
    PROC_REPEAT(sub_80CA3B8),
    PROC_CALL(sub_80C9EE8),
    PROC_REPEAT(sub_80C9F7C),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C883C),
    PROC_CALL(sub_80C9DF0),
    PROC_REPEAT(sub_80C9E6C),
    PROC_CALL(sub_80C9C5C),
    PROC_REPEAT(sub_80C9FF8),
    PROC_REPEAT(sub_80CA10C),
    PROC_REPEAT(sub_80CA3B8),
    PROC_CALL(sub_80C9EE8),
    PROC_REPEAT(sub_80C9F7C),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C8934),
    PROC_CALL(sub_80C9DF0),
    PROC_REPEAT(sub_80C9E6C),
    PROC_CALL(sub_80C9CAC),
    PROC_REPEAT(sub_80C9FF8),
    PROC_REPEAT(sub_80CA10C),
    PROC_REPEAT(sub_80CA3B8),
    PROC_CALL(sub_80C9EE8),
    PROC_REPEAT(sub_80C9F7C),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C8A20),
    PROC_CALL(sub_80C9DF0),
    PROC_REPEAT(sub_80C9E6C),
    PROC_CALL(sub_80C9CFC),
    PROC_REPEAT(sub_80C9FF8),
    PROC_REPEAT(sub_80CA10C),
    PROC_REPEAT(sub_80CA3B8),
    PROC_CALL(sub_80C9EE8),
    PROC_REPEAT(sub_80C9F7C),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C8B0C),
    PROC_CALL(sub_80C9DF0),
    PROC_REPEAT(sub_80C9E6C),
    PROC_CALL(sub_80C9D4C),
    PROC_REPEAT(sub_80C9FF8),
    PROC_REPEAT(sub_80CA10C),
    PROC_REPEAT(sub_80CA3B8),
    PROC_CALL(sub_80C9EE8),
    PROC_REPEAT(sub_80C9F7C),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C8BF8),
    PROC_CALL(sub_80C9DF0),
    PROC_REPEAT(sub_80C9E6C),
    PROC_CALL(sub_80C9DA0),
    PROC_REPEAT(sub_80C9FF8),
    PROC_REPEAT(sub_80CA10C),
    PROC_REPEAT(sub_80CA3B8),
    PROC_CALL(sub_80C9EE8),
    PROC_REPEAT(sub_80C9F7C),
    PROC_CALL(sub_80C8564),
    PROC_START_CHILD(gUnkData_99),
    PROC_END_EACH(gUnkData_97),
    PROC_REPEAT(sub_80C8CE4),
    PROC_CALL(sub_80CA4A4),
    PROC_REPEAT(sub_80CA4DC),
    PROC_CALL(sub_80CA92C),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C8D30),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CAA38),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C8ED4),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CABB0),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C9024),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CAE20),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C9100),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CAF2C),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C9218),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CB0A0),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C9330),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CB20C),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C940C),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CB320),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C955C),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CB594),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C9638),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CB6A0),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C9750),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CB878),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C98A0),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CBA64),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C99B8),
    PROC_CALL(sub_80CA940),
    PROC_REPEAT(sub_80CBC40),
    PROC_CALL(sub_80C8564),
    PROC_REPEAT(sub_80C9A94),
    PROC_REPEAT(sub_80C9AFC),
    PROC_SLEEP(32),
    PROC_LABEL(99),
    PROC_CALL(sub_80CBD7C),
    PROC_SLEEP(1),
    PROC_END,
};
