#include "global.h"

/* Arrays expanded from binary files at build time by tools/preproc
 * (run before cpp). Migrated in-place from data/data_titlescreen.s into .data.
 */

u8 gGfx_TitleMainBackground_1[] = INCBIN_U8("graphics/titlescreen/title_main_background_1.4bpp.lz");
u8 gGfx_TitleMainBackground_2[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_main_background_2.4bpp.lz");
u8 gTsa_TitleMainBackground[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_main_background.map.bin.lz");
u16 gPal_TitleMainBackground[] = INCBIN_U16("graphics/titlescreen/title_main_background.gbapal");
u8 gGfx_TitleDragonForeground[] = INCBIN_U8("graphics/titlescreen/title_dragon_foreground.4bpp.lz");
u8 gTsa_TitleDragonForeground[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_dragon_foreground.map.bin.lz");
u16 gPal_TitleDragonForeground[] = INCBIN_U16("graphics/titlescreen/title_dragon_foreground.gbapal");
u8 gGfx_FireEmblemLogo[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_fire_emblem_logo.4bpp.lz");
u8 gGfx_SubtitlePressStart[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_logos.4bpp.lz");
u16 gPal_PressStart[] __attribute__((aligned(4))) = INCBIN_U16("graphics/titlescreen/title_press_start.gbapal","graphics/titlescreen/title_copyright.gbapal","graphics/titlescreen/title_fire_emblem_logo.gbapal","graphics/titlescreen/title_sacred_stones_banner.gbapal");
u16 gPal_08AADBE8[] = INCBIN_U16("graphics/titlescreen/title_unk_palette_1.gbapal");
u8 gGfx_08AADC08[] = INCBIN_U8("graphics/titlescreen/title_unk_image_1.4bpp.lz");
u8 gTsa_08AAE61C[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_unk_image_1.map.bin.lz");
u16 gPal_08AAE8CC[] = INCBIN_U16("graphics/titlescreen/title_unk_image_1.gbapal");
u8 gGfx_08AAE8EC[] = INCBIN_U8("graphics/titlescreen/title_unk_image_2.4bpp.lz");
u8 gTsa_08AAF928[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_unk_image_2.map.bin.lz");
u16 gPal_08AAFCF4[] = INCBIN_U16("graphics/titlescreen/title_unk_image_2.gbapal");
u8 gGfx_08AAFD14[] = INCBIN_U8("graphics/titlescreen/title_unk_image_3.4bpp.lz");
u8 gTsa_08AAFF10[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_unk_image_3.map.bin.lz");
u16 gPal_08AB0114[] = INCBIN_U16("graphics/titlescreen/title_unk_image_3.gbapal");
u8 gGfx_08AB0134[] = INCBIN_U8("graphics/titlescreen/title_unk_image_4.4bpp.lz");
u8 gTsa_08AB0A20[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_unk_image_4.map.bin.lz");
u16 gPal_08AB0B24[] = INCBIN_U16("graphics/titlescreen/title_unk_image_4.gbapal");
u8 gGfx_TitleDemonKing[] = INCBIN_U8("graphics/titlescreen/title_demon_king.4bpp.lz");
u8 gTsa_TitleDemonKing[] __attribute__((aligned(4))) = INCBIN_U8("graphics/titlescreen/title_demon_king.map.bin.lz");
u16 gPal_TitleDemonKing[] = INCBIN_U16("graphics/titlescreen/title_demon_king.gbapal");
u8 gGfx_TitleLargeGlowingOrb[] = INCBIN_U8("graphics/titlescreen/title_large_glowing_orb.4bpp.lz");
u16 gPal_TitleLargeGlowingOrb[] __attribute__((aligned(4))) = INCBIN_U16("graphics/titlescreen/title_large_glowing_orb.gbapal","graphics/titlescreen/title_unk_palette_2.gbapal","graphics/titlescreen/title_unk_palette_3.gbapal");
u8 gGfx_TitleSmallLightBubbles[] = INCBIN_U8("graphics/titlescreen/title_small_light_bubbles.4bpp.lz");
u16 gPal_TitleSmallLightBubbles[] __attribute__((aligned(4))) = INCBIN_U16("graphics/titlescreen/title_small_light_bubbles.gbapal");
