#include "global.h"

/* Arrays expanded from binary files at build time by tools/preproc (run
 * before cpp). Migrated in-place from data/generic_icon.s into .data.
 */

u8 CONST_DATA gUnknown_08599734[] = INCBIN_U8("graphics/generic_icon/0.4bpp");
u8 CONST_DATA gUnknown_08599934[] = INCBIN_U8("graphics/generic_icon/1.4bpp");
u8 CONST_DATA gUnknown_08599B34[] = INCBIN_U8("graphics/generic_icon/2.4bpp");
u8 CONST_DATA gUnknown_08599D34[] = INCBIN_U8("graphics/generic_icon/3.4bpp");
u16 CONST_DATA gUnknown_08599F34[] = INCBIN_U16("graphics/generic_icon/0.gbapal");
u16 CONST_DATA gUnknown_08599F54[] = INCBIN_U16("graphics/generic_icon/1.gbapal");
u16 CONST_DATA gUnknown_08599F74[] = INCBIN_U16("graphics/generic_icon/2.gbapal", "graphics/generic_icon/3.gbapal");
