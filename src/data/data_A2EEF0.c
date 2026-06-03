#include "global.h"

/* Arrays expanded from binary files at build time by tools/preproc (run
 * before cpp). Migrated in-place from data/data_A2EEF0.s into .data.
 */

u8 gUnkData_81[] = INCBIN_U8("graphics/misc/gUnknown_08A301B0.4bpp.lz");
u16 gUnkData_82[] = INCBIN_U16("graphics/misc/gUnknown_08A30780.4bpp");
u8 gUnkData_83[] = INCBIN_U8("graphics/misc/gUnknown_08A30800.4bpp.lz");
u16 gUnkData_84[] = INCBIN_U16("graphics/misc/gUnknown_08A30978.tsa.bin");
u8 gUnkData_85[] = INCBIN_U8("graphics/misc/gUnknown_08A30E2C.4bpp.lz");
u8 gUnkData_86[] = INCBIN_U8("graphics/misc/gUnknown_08A35488.tsa.bin");
u16 gUnkData_87[] = INCBIN_U16("graphics/misc/gUnknown_08A3593C.4bpp");
u8 gUnkData_88[] = INCBIN_U8("graphics/misc/gUnknown_08A35A3C.4bpp.lz");
u8 gUnkData_89[] = INCBIN_U8("graphics/misc/gUnknown_08A35FD0.tsa.bin.lz");
u16 gUnkData_90[] = INCBIN_U16("graphics/misc/gUnknown_08A360C8.4bpp");
u8 gUnkData_91[] = INCBIN_U8("graphics/misc/gUnknown_08A360E8.4bpp.lz");
u8 gUnkData_92[] = INCBIN_U8("graphics/misc/gUnknown_08A36284.tsa.bin.lz");
u16 gUnkData_93[] = INCBIN_U16("graphics/misc/gUnknown_08A36318.gbapal");
u8 gUnkData_94[] = INCBIN_U8("graphics/misc/gUnknown_08A36338.4bpp.lz");
u8 gUnkData_95[] = INCBIN_U8("graphics/misc/gUnknown_08A372C0.4bpp");
u16 gUnkData_96[] = INCBIN_U16("graphics/misc/gUnknown_08A37300.agbpal");

/* Class-reel (promotion-info) letter/glyph font: 64 LZ77-compressed 4bpp glyph blocks,
 * character-indexed via gOpinfo_1[*str] and decompressed at runtime (src/opinfo.c).
 * Was hidden trailing data baked onto gUnknown_08A37300.agbpal after its 16-colour
 * palette; carved out so gUnkData_96 is a clean palette (ROM byte-identical). */
u8 gOpinfoLetterGfx[] = INCBIN_U8("graphics/misc/gOpinfoLetterGfx.bin");
