#include "global.h"
#include "anime.h"
#include "gba_sprites.h"

extern struct AnimSpriteData AnimSprite_08759684[];
extern struct AnimSpriteData AnimSprite_0875975C[];
extern struct AnimSpriteData AnimSprite_0875981C[];
extern struct AnimSpriteData AnimSprite_087598DC[];
extern struct AnimSpriteData AnimSprite_0875999C[];
extern struct AnimSpriteData AnimSprite_08759A5C[];
extern AnimScr AnimScr_TriAtkLeft[];
extern struct AnimSpriteData AnimSprite_08759B50[];
extern struct AnimSpriteData AnimSprite_08759C10[];
extern struct AnimSpriteData AnimSprite_08759CD0[];
extern struct AnimSpriteData AnimSprite_08759D90[];
extern AnimScr AnimScr_TriAtkRight[];
extern struct AnimSpriteData AnimSprite_08759E7C[];
extern struct AnimSpriteData AnimSprite_08759EE8[];
extern AnimScr AnimScr_TriKnightOBJ[];
extern struct AnimSpriteData AnimSprite_08759F0C[];
extern struct AnimSpriteData AnimSprite_08759F78[];
extern AnimScr AnimScr_TriGenerialLanceOBJ[];
extern struct AnimSpriteData AnimSprite_08759F9C[];
extern struct AnimSpriteData AnimSprite_0875A008[];
extern AnimScr AnimScr_TriGenerialAxeOBJ[];
extern struct AnimSpriteData AnimSprite_0875A034[];
extern struct AnimSpriteData AnimSprite_0875A094[];
extern AnimScr AnimScr_TriGenerialHandAxeOBJ[];
extern struct AnimSpriteData AnimSprite_0875A0B8[];
extern struct AnimSpriteData AnimSprite_0875A118[];
extern struct AnimSpriteData AnimSprite_0875A184[];
extern AnimScr AnimScr_TriKnightAtkOBJ[];
extern struct AnimSpriteData AnimSprite_0875A200[];
extern struct AnimSpriteData AnimSprite_0875A278[];
extern struct AnimSpriteData AnimSprite_0875A2FC[];
extern AnimScr AnimScr_TriGenerialLanceAtkOBJ[];
extern struct AnimSpriteData AnimSprite_0875A378[];
extern struct AnimSpriteData AnimSprite_0875A3CC[];
extern struct AnimSpriteData AnimSprite_0875A450[];
extern AnimScr AnimScr_TriGenerialAxeAtkOBJ[];
extern struct AnimSpriteData AnimSprite_0875A4CC[];
extern struct AnimSpriteData AnimSprite_0875A508[];
extern struct AnimSpriteData AnimSprite_0875A598[];
extern struct AnimSpriteData AnimSprite_0875A5B0[];
extern struct AnimSpriteData AnimSprite_0875A640[];
extern struct AnimSpriteData AnimSprite_0875A6C4[];
extern AnimScr AnimScr_TriGenerialHandAxeAtkOBJ[];

struct AnimSpriteData CONST_DATA AnimSprite_08759684[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0xA20 / 0x20, +0x26, -0x37 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0xA60 / 0x20, +0x36, -0x37 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0xA00 / 0x20, +0x28, -0x3F } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0xA80 / 0x20, +0x30, -0x47 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0xE00 / 0x20, +0x1E, -0x2F } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0xF60 / 0x20, +0x18, -0x27 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x800 / 0x20, +0x07, -0x17 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x880 / 0x20, +0x27, -0x17 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x8A0 / 0x20, +0x00, -0x27 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x920 / 0x20, +0x08, -0x37 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x9A0 / 0x20, +0x20, -0x1F } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0xDA0 / 0x20, +0x28, -0x3C } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x9E0 / 0x20, +0x00, -0x30 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0xAC0 / 0x20, +0x03, -0x4F } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0xB00 / 0x20, +0x03, -0x3F } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0xB40 / 0x20, +0x13, -0x3F } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0xB60 / 0x20, +0x0B, -0x2F } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875975C[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0019, 34, -60 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x001D, 54, -76 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x003F, 46, -68 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 15, -39 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 47, -39 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 8, -55 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 16, -71 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 40, -47 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 48, -76 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 8, -64 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0017, 12, -87 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0038, 20, -79 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0018, 28, -79 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0014, 9, -71 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0016, 25, -71 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875981C[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0023, 36, -60 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0004, 42, -68 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0006, 58, -68 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0027, 60, -52 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0007, 52, -52 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 20, -46 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 52, -46 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 13, -62 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 21, -78 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 45, -54 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 53, -83 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 13, -71 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0000, 5, -72 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0002, 21, -72 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0003, 29, -69 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_087598DC[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0023, 39, -64 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0004, 45, -72 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0006, 61, -72 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0027, 63, -56 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0007, 55, -56 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 23, -50 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 55, -50 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 16, -66 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 24, -82 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 48, -58 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 56, -87 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 16, -75 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0000, 8, -76 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0002, 24, -76 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0003, 32, -73 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875999C[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x000D, 42, -71 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0011, 51, -79 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0013, 67, -79 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0031, 59, -55 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 26, -54 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 58, -54 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 19, -70 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 27, -86 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 51, -62 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 59, -91 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 19, -79 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000C, 37, -78 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0009, 13, -87 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000B, 29, -87 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0008, 13, -71 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759A5C[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0051, 58, -89 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0053, 74, -89 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0050, 60, -97 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0054, 68, -105 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0070, 50, -81 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x007B, 44, -73 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 27, -57 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 59, -57 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 20, -73 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 28, -89 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 52, -65 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 60, -94 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 20, -82 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0056, 23, -113 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0058, 23, -97 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x005A, 39, -97 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x005B, 31, -81 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriAtkLeft[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759684, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875975C, 2),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875981C, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_087598DC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875999C, 3),
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759A5C, 10),
    ANIMSCR_BLOCKED,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759B50[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0019, 42, -5 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x001D, 62, -21 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x003F, 54, -13 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 23, 16 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 55, 16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 16, 0 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 24, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 48, 8 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 56, -21 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 16, -9 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0017, 20, -32 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0038, 28, -24 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0018, 36, -24 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0014, 17, -16 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0016, 33, -16 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759C10[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0019, 48, 1 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x001D, 68, -15 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x003F, 60, -7 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 29, 22 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 61, 22 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 22, 6 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 30, -10 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 54, 14 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 62, -15 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 22, -3 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0017, 26, -26 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0038, 34, -18 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0018, 42, -18 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0014, 23, -10 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0016, 39, -10 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759CD0[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x000D, 57, 4 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0011, 66, -4 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0013, 82, -4 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0031, 74, 20 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 41, 21 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 73, 21 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 34, 5 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 42, -11 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 66, 13 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 74, -16 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 34, -4 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000C, 52, -3 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0009, 28, -12 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000B, 44, -12 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0008, 28, 4 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759D90[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0051, 74, -8 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0053, 90, -8 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0050, 76, -16 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0054, 84, -24 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0070, 66, 0 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x007B, 60, 8 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, 43, 24 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, 75, 24 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, 36, 8 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, 44, -8 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, 68, 16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006D, 76, -13 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, 36, -1 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0056, 39, -32 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0058, 39, -16 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x005A, 55, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x005B, 47, 0 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriAtkRight[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759B50, 2),
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759C10, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759CD0, 2),
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759D90, 12),
    ANIMSCR_BLOCKED,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759E7C[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0000, -12, -24 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0006, -21, -8 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000A, 11, -8 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0004, -37, 0 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0024, -34, 8 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000B, 20, -22 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x002B, -13, 8 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0025, -20, -16 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759EE8[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x001F, 0, -8 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriKnightOBJ[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759E7C, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759EE8, 1),
    ANIMSCR_LOOP,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759F0C[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0000, -13, -32 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0004, -13, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0008, -13, 0 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x000C, -37, -4 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000E, -21, -4 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x002F, 19, -17 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0030, -21, -17 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000F, 19, -25 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759F78[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x001F, 0, -8 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriGenerialLanceOBJ[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759F0C, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759F78, 1),
    ANIMSCR_LOOP,
};

struct AnimSpriteData CONST_DATA AnimSprite_08759F9C[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0000, -34, -31 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0004, -2, -31 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0006, 14, -31 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0007, -34, -15 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x000B, -2, -15 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000D, 14, -15 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x000E, -18, 1 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0012, 14, 1 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A008[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x001F, 0, -8 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriGenerialAxeOBJ[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759F9C, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A008, 1),
    ANIMSCR_LOOP,
    ANIMSCR_FORCE_SPRITE(AnimSprite_08759F9C, 4),
    ANIMSCR_LOOP,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A034[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0000, -12, -32 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0004, -12, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0008, -12, 0 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x000C, -28, -24 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000E, -28, -8 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0010, 20, -16 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0011, -20, 8 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A094[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x001F, 0, -8 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriGenerialHandAxeOBJ[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A034, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A094, 1),
    ANIMSCR_LOOP,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A0B8[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0000, -21, -27 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0004, -21, -11 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0028, -13, 5 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x002A, 3, 5 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000B, 11, -19 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0009, 19, -11 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0008, -29, -13 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A118[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0039, -30, 4 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0013, -36, -12 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0017, -4, -12 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x000F, -35, -28 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0019, -3, -20 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x001B, -43, -14 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x002D, -59, -12 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000D, -67, -13 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A184[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x001F, 0, -8 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriKnightAtkOBJ[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A0B8, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A0B8, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A0B8, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A0B8, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A0B8, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A0B8, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A118, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A118, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A118, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A118, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A118, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A118, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A184, 1),
    ANIMSCR_BLOCKED,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A200[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0000, -32, -16 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0004, 0, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0006, -22, 0 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000A, 10, 0 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x000B, -3, -32 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000D, -19, -24 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000F, -11, -32 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x002D, 16, -16 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x002F, 32, -16 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A278[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, -39, 0 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0044, -7, 0 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0045, -39, -16 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0049, -7, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x004B, -32, -32 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x006F, 0, -24 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x0050, -79, -16 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004F, -87, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0052, -63, -13 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0054, -47, -13 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A2FC[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x001F, 0, -8 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriGenerialLanceAtkOBJ[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A200, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A200, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A200, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A200, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A200, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A200, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A278, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A278, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A278, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A278, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A278, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A278, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A2FC, 1),
    ANIMSCR_BLOCKED,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A378[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0000, -26, -32 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0004, 6, -32 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0005, -24, -16 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0009, 8, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x000A, -22, 0 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000E, 10, 0 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A3CC[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, -68, -24 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0044, -36, -32 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0048, -4, -32 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0049, -36, -16 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, -4, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x004E, -38, 0 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0052, -6, 0 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0056, 4, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0053, -60, -8 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0055, -44, -8 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A450[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x001F, 0, -8 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriGenerialAxeAtkOBJ[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A378, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A378, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A378, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A378, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A378, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A378, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A3CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A3CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A3CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A3CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A3CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A3CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A450, 1),
    ANIMSCR_BLOCKED,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A4CC[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0000, -17, -32 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0004, -17, -16 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0008, -17, 0 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x000C, 15, -2 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A508[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, -32, -31 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0044, -32, -15 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0048, -32, 1 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004C, -40, -23 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, -40, -7 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0054, -40, 9 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004E, 0, -23 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0055, 0, -7 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x004F, -72, -19 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0051, -80, -21 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0052, -72, -27 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A598[] =
{
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x005F, 0, -8 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A5B0[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, -32, -31 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0044, -32, -15 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0048, -32, 1 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004C, -40, -23 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, -40, -7 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0054, -40, 9 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004E, 0, -23 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0055, 0, -7 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x004F, -104, -19 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0051, -112, -21 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0052, -104, -27 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A640[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, -32, -31 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0044, -32, -15 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0048, -32, 1 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004C, -40, -23 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, -40, -7 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0054, -40, 9 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004E, 0, -23 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0055, 0, -7 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_16) << 16), .as = { .object = { 0x004F, -128, -19 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0052, -128, -27 } } },
    ANIM_SPRITE_END,
};

struct AnimSpriteData CONST_DATA AnimSprite_0875A6C4[] =
{
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0040, -32, -31 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0044, -32, -15 } } },
    { .header = (u32)(ATTR0_WIDE) | ((u32)(ATTR1_SIZE_32) << 16), .as = { .object = { 0x0048, -32, 1 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004C, -40, -23 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004D, -40, -7 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0054, -40, 9 } } },
    { .header = (u32)(ATTR0_TALL) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x004E, 0, -23 } } },
    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), .as = { .object = { 0x0055, 0, -7 } } },
    ANIM_SPRITE_END,
};

AnimScr CONST_DATA AnimScr_TriGenerialHandAxeAtkOBJ[] =
{
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A4CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A4CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A4CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A4CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A4CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A4CC, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A508, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A5B0, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A640, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A6C4, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A6C4, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A6C4, 1),
    ANIMSCR_FORCE_SPRITE(AnimSprite_0875A598, 1),
    ANIMSCR_BLOCKED,
};
