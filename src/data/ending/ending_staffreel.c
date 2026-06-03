#include "global.h"

u8 Img_StaffReelEnt_0[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A40FE8.4bpp.lz");

u8 Img_StaffReelEnt_1[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A41B30.4bpp.lz");

u8 Img_StaffReelEnt_2[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A42748.4bpp.lz");

u8 Img_StaffReelEnt_3[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A432C0.4bpp.lz");

u8 Img_StaffReelEnt_4[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A43CBC.4bpp.lz");

u8 Img_StaffReelEnt_5[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A45150.4bpp.lz");

u8 Img_StaffReelEnt_6[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A4561C.4bpp.lz");

u8 Img_StaffReelEnt_7[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A45F58.4bpp.lz");

u8 Img_StaffReelEnt_8[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A46988.4bpp.lz");

u8 Img_StaffReelEnt_9[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A472B0.4bpp.lz");

u8 Img_StaffReelEnt_10[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A48744.4bpp.lz");

u8 Img_StaffReelEnt_11[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A497A8.4bpp.lz");

u8 Img_StaffReelEnt_12[] = INCBIN_U8("graphics/misc/Img_StaffReelEnt_08A4A9D4.4bpp.lz");

u8 Tsa_StaffReelEnt_0[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4AE08.tsa.bin.lz");

u8 Tsa_StaffReelEnt_1[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4B090.tsa.bin.lz");

u8 Tsa_StaffReelEnt_2[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4B2F4.tsa.bin.lz");

u8 Tsa_StaffReelEnt_3[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4B558.tsa.bin.lz");

u8 Tsa_StaffReelEnt_4[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4B788.tsa.bin.lz");

u8 Tsa_StaffReelEnt_5[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4BB50.tsa.bin.lz");

u8 Tsa_StaffReelEnt_6[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4BCC4.tsa.bin.lz");

u8 Tsa_StaffReelEnt_7[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4BEC0.tsa.bin.lz");

u8 Tsa_StaffReelEnt_8[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4C0E4.tsa.bin.lz");

u8 Tsa_StaffReelEnt_9[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4C308.tsa.bin.lz");

u8 Tsa_StaffReelEnt_10[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4C6EC.tsa.bin.lz");

u8 Tsa_StaffReelEnt_11[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4C9F0.tsa.bin.lz");

u8 Tsa_StaffReelEnt_12[] = INCBIN_U8("graphics/misc/Tsa_StaffReelEnt_08A4CD40.tsa.bin.lz");

u8 gGfx_BrownTextBox[] = INCBIN_U8("graphics/misc/gGfx_BrownTextBox.4bpp.lz");

u16 gPal_BrownTextBox[] = INCBIN_U16("graphics/misc/gPal_BrownTextBox.agbpal");

/* Brown-text-box sprite object animation (FE8 AP format; see include/ap.h), hidden after
 * gPal_BrownTextBox's 16-colour palette. An AP definition u16[] -- offset table then
 * per-frame [objCount][obj OAM attr0/attr1/attr2] -- the same raw form as every other AP
 * definition (e.g. SpriteAnim_GasTrapVertical); no struct/macro form exists for it. */
u16 SpriteAnim_BrownTextBox[] =
{
    0x0004, 0x0008, 0x0008, 0x0082, 0x00DA, 0x00E6, 0x0014, 0x4000,
    0x4000, 0x0000, 0x4000, 0x4040, 0x0002, 0x4000, 0x0020, 0x0002,
    0x4018, 0x4000, 0x000D, 0x4018, 0x4040, 0x000F, 0x4018, 0x0020,
    0x000F, 0x4008, 0x4000, 0x0006, 0x4008, 0x4040, 0x0007, 0x0010,
    0x0000, 0x000B, 0x0010, 0x0058, 0x000C, 0x4008, 0x0020, 0x0007,
    0x4010, 0x0008, 0x0007, 0x4010, 0x0018, 0x0007, 0x4010, 0x0038,
    0x0007, 0x4010, 0x0048, 0x0007, 0x4010, 0x0038, 0x0007, 0x4010,
    0x0028, 0x0007, 0x4008, 0x0030, 0x0007, 0x4000, 0x0030, 0x0002,
    0x4018, 0x0030, 0x000F, 0x000F, 0x4000, 0x4000, 0x0000, 0x4000,
    0x4030, 0x0002, 0x4000, 0x0020, 0x0002, 0x4018, 0x4000, 0x000D,
    0x4018, 0x4030, 0x000F, 0x4018, 0x0020, 0x000F, 0x4008, 0x4000,
    0x0006, 0x4008, 0x4030, 0x0007, 0x0010, 0x0000, 0x000B, 0x0010,
    0x0048, 0x000C, 0x4008, 0x0020, 0x0007, 0x4010, 0x0008, 0x0007,
    0x4010, 0x0018, 0x0007, 0x4010, 0x0038, 0x0007, 0x4010, 0x0028,
    0x0007, 0x0004, 0x0000, 0x0000, 0x0000, 0x0000, 0xFFFF, 0x0004,
    0x0001, 0x0000, 0x0000, 0x0000, 0xFFFF, 0x0000,
};
