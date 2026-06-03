#include "global.h"
#include "anime.h"
#include "gba_sprites.h"

extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_0[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_1[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_2[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_3[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_4[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_5[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_6[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_7[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_8[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_9[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_10[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_11[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_12[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_13[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_14[];
extern struct AnimSpriteData AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_15[];
extern AnimScr AnimScr_EfxLvupOBJ2[];

u8 CONST_DATA Img_LvupApfx[] = INCBIN_U8("./graphics/lvup/LvupAp.4bpp.lz");

u16 CONST_DATA Pal_LvupApfx[] = INCBIN_U16("./graphics/lvup/LvupAp.gbapal", 0x0, 0x20);

u16 CONST_DATA gEfxlvupfx_0[] = INCBIN_U16("graphics/banim/efxlvupfx/gUnknown_085BB2FC.bin");

u8 CONST_DATA Img_ArenaBattleBg[] = INCBIN_U8("graphics/banim/efxlvupfx/Img_ArenaBattleBg.4bpp.lz");

u8 CONST_DATA Tsa_ArenaBattleBg[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa_ArenaBattleBg.map.bin.lz");

u16 CONST_DATA Pal_ArenaBattleBg_A[] = INCBIN_U16("graphics/banim/efxlvupfx/Pal_ArenaBattleBg_A.gbapal");

u16 CONST_DATA Pal_ArenaBattleBg_B[] = INCBIN_U16("graphics/banim/efxlvupfx/Pal_ArenaBattleBg_B.gbapal");

u16 CONST_DATA Pal_ArenaBattleBg_C[] = INCBIN_U16("graphics/banim/efxlvupfx/Pal_ArenaBattleBg_C.gbapal");

u8 CONST_DATA gEfxlvupfx_1[] = INCBIN_U8("graphics/banim/efxlvupfx/gUnknown_085BF114.4bpp.lz");

u16 CONST_DATA gEfxlvupfx_2[] = INCBIN_U16("graphics/banim/efxlvupfx/gUnknown_085BF4FC.agbpal");

u8 CONST_DATA gEfxlvupfx_3[] = INCBIN_U8("graphics/banim/efxlvupfx/gUnknown_085BF5FC.map.bin.lz");

u8 CONST_DATA Img1_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Img1_EfxLvupBG.4bpp.lz");

u8 CONST_DATA Img2_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Img2_EfxLvupBG.4bpp.lz");

u8 CONST_DATA Img3_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Img3_EfxLvupBG.4bpp.lz");

u8 CONST_DATA Img4_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Img4_EfxLvupBG.4bpp.lz");

u8 CONST_DATA Img5_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Img5_EfxLvupBG.4bpp.lz");

u8 CONST_DATA Img6_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Img6_EfxLvupBG.4bpp.lz");

u8 CONST_DATA Img7_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Img7_EfxLvupBG.4bpp.lz");

u16 CONST_DATA Pal_EfxLvupBG[] = INCBIN_U16("graphics/banim/efxlvupfx/Pal_EfxLvupBG.gbapal");

u8 CONST_DATA Tsa1_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa1_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa2_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa2_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa3_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa3_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa4_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa4_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa5_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa5_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa6_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa6_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa7_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa7_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa8_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa8_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa9_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa9_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa10_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa10_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Tsa11_EfxLvupBG[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa11_EfxLvupBG.map.bin.lz");

u8 CONST_DATA Img_EfxLvupBG2[] = INCBIN_U8("graphics/banim/efxlvupfx/Img_EfxLvupBG2.4bpp.lz");

u16 CONST_DATA Pal_EfxLvupBG2[] = INCBIN_U16("graphics/banim/efxlvupfx/Pal_EfxLvupBG2.gbapal");

u16 CONST_DATA Pal_EfxLvupBGCOL[] = INCBIN_U16("graphics/banim/efxlvupfx/Pal_EfxLvupBGCOL.gbapal");

u8 CONST_DATA Tsa1_EfxLvupBG2[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa1_EfxLvupBG2.map.bin.lz");

u8 CONST_DATA Tsa2_EfxLvupBG2[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa2_EfxLvupBG2.map.bin.lz");

u8 CONST_DATA Tsa3_EfxLvupBG2[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa3_EfxLvupBG2.map.bin.lz");

u8 CONST_DATA Tsa4_EfxLvupBG2[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa4_EfxLvupBG2.map.bin.lz");

u8 CONST_DATA Tsa5_EfxLvupBG2[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa5_EfxLvupBG2.map.bin.lz");

u8 CONST_DATA Tsa6_EfxLvupBG2[] = INCBIN_U8("graphics/banim/efxlvupfx/Tsa6_EfxLvupBG2.map.bin.lz");

u8 CONST_DATA Img_EfxLvupOBJ2[] = INCBIN_U8("graphics/banim/efxlvupfx/Img_EfxLvupOBJ2.4bpp.lz");

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_0[] =
{
    { .header = (u32)(0x8000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0002, -120, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -104, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -112, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -88, -40 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_1[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, -120, -48 } } },
    { .header = (u32)(0x4000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -88, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -96, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -72, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -112, -24 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_2[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, -104, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -120, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -72, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -80, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -56, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -104, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -96, -24 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_3[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, -88, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -104, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -56, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -64, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -40, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -112, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -88, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -80, -24 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_4[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, -72, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -88, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -40, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -48, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -24, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -96, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -72, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -64, -24 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_5[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, -56, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -72, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -24, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -32, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -8, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -80, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -56, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -48, -24 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_6[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, -40, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -56, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -8, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -16, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 8, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -64, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -32, -24 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -40, -48 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_7[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, -24, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -40, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 8, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 0, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 24, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -48, -32 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_8[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, -8, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -24, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 24, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 16, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 40, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -32, -32 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_9[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, 8, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, -8, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0004, 8, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 40, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 32, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 56, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, -16, -32 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_10[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, 24, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 8, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 8, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 56, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 48, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 72, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 0, -32 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_11[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, 40, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 24, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 8, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0004, 40, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 72, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 64, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 88, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 16, -32 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_12[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, 56, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 40, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 8, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 40, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 88, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 80, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 104, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 32, -32 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_13[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, 72, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 56, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 8, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 40, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0004, 72, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 104, -40 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 96, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 48, -32 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_14[] =
{
    { .header = (u32)(0x0000) | ((u32)(0x8000) << 16), .as = { .object = { 0x0000, 88, -48 } } },
    { .header = (u32)(0x0000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0024, 72, -40 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 8, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 40, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 72, -36 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 112, -32 } } },
    { .header = (u32)(0x0000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0026, 64, -32 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

struct AnimSpriteData CONST_DATA AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_15[] =
{
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -120, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -88, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -56, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, -24, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 8, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 40, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x4000) << 16), .as = { .object = { 0x0004, 72, -36 } } },
    { .header = (u32)(0x4000) | ((u32)(0x0000) << 16), .as = { .object = { 0x0004, 104, -36 } } },
    { .header = (u32)(0x0001) | ((u32)(0x0000) << 16), .as = { .object = { 0x0000, 0, 0 } } },
};

AnimScr CONST_DATA AnimScr_EfxLvupOBJ2[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_0, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_1, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_2, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_3, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_4, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_5, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_6, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_7, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_8, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_9, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_10, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_11, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_12, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_13, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_14, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_EfxLvupOBJ2_EfxLvupOBJ_15, 10),
    ANIMSCR_BLOCKED,
};
