    .include "animscr.inc"
	.section .data

	.global Img_LvupApfx
Img_LvupApfx:  @ 0x085BB0C8
	.incbin "./graphics/lvup/LvupAp.4bpp.lz"

	.global Pal_LvupApfx
Pal_LvupApfx:  @ 0x085BB2DC
	.incbin "./graphics/lvup/LvupAp.gbapal", 0x0, 0x20

	.global gUnknown_085BB2FC
gUnknown_085BB2FC:  @ 0x085BB2FC
	.incbin "graphics/banim/efxlvupfx/gUnknown_085BB2FC.agbpal"

	.global Img_ArenaBattleBg
Img_ArenaBattleBg:  @ 0x085BC188
	.incbin "graphics/banim/efxlvupfx/Img_ArenaBattleBg.4bpp.lz"

	.global Tsa_ArenaBattleBg
Tsa_ArenaBattleBg:  @ 0x085BE7F4
	.incbin "graphics/banim/efxlvupfx/Tsa_ArenaBattleBg.map.bin.lz"

	.global Pal_ArenaBattleBg_A
Pal_ArenaBattleBg_A:  @ 0x085BEF94
	.incbin "graphics/banim/efxlvupfx/Pal_ArenaBattleBg_A.gbapal"

	.global Pal_ArenaBattleBg_B
Pal_ArenaBattleBg_B:  @ 0x085BF014
	.incbin "graphics/banim/efxlvupfx/Pal_ArenaBattleBg_B.gbapal"

	.global Pal_ArenaBattleBg_C
Pal_ArenaBattleBg_C:  @ 0x085BF094
	.incbin "graphics/banim/efxlvupfx/Pal_ArenaBattleBg_C.gbapal"

	.global gUnknown_085BF114
gUnknown_085BF114:  @ 0x085BF114
	.incbin "graphics/banim/efxlvupfx/gUnknown_085BF114.4bpp.lz"

	.global gUnknown_085BF4FC
gUnknown_085BF4FC:  @ 0x085BF4FC
	.incbin "graphics/banim/efxlvupfx/gUnknown_085BF4FC.agbpal"

	.global gUnknown_085BF5FC
gUnknown_085BF5FC:  @ 0x085BF5FC
	.incbin "graphics/banim/efxlvupfx/gUnknown_085BF5FC.map.bin.lz"

	.global Img1_EfxLvupBG
Img1_EfxLvupBG:
	.incbin "graphics/banim/efxlvupfx/Img1_EfxLvupBG.4bpp.lz"

	.global Img2_EfxLvupBG
Img2_EfxLvupBG:
	.incbin "graphics/banim/efxlvupfx/Img2_EfxLvupBG.4bpp.lz"

	.global Img3_EfxLvupBG
Img3_EfxLvupBG:
	.incbin "graphics/banim/efxlvupfx/Img3_EfxLvupBG.4bpp.lz"

	.global Img4_EfxLvupBG
Img4_EfxLvupBG:
	.incbin "graphics/banim/efxlvupfx/Img4_EfxLvupBG.4bpp.lz"

	.global Img5_EfxLvupBG
Img5_EfxLvupBG:
	.incbin "graphics/banim/efxlvupfx/Img5_EfxLvupBG.4bpp.lz"

	.global Img6_EfxLvupBG
Img6_EfxLvupBG:
	.incbin "graphics/banim/efxlvupfx/Img6_EfxLvupBG.4bpp.lz"

	.global Img7_EfxLvupBG
Img7_EfxLvupBG:
	.incbin "graphics/banim/efxlvupfx/Img7_EfxLvupBG.4bpp.lz"

	.global Pal_EfxLvupBG
Pal_EfxLvupBG:  @ 0x085C48AC
	.incbin "graphics/banim/efxlvupfx/Pal_EfxLvupBG.gbapal"

    .global Tsa1_EfxLvupBG
Tsa1_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa1_EfxLvupBG.map.bin.lz"

    .global Tsa2_EfxLvupBG
Tsa2_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa2_EfxLvupBG.map.bin.lz"

    .global Tsa3_EfxLvupBG
Tsa3_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa3_EfxLvupBG.map.bin.lz"

    .global Tsa4_EfxLvupBG
Tsa4_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa4_EfxLvupBG.map.bin.lz"

    .global Tsa5_EfxLvupBG
Tsa5_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa5_EfxLvupBG.map.bin.lz"

    .global Tsa6_EfxLvupBG
Tsa6_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa6_EfxLvupBG.map.bin.lz"

    .global Tsa7_EfxLvupBG
Tsa7_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa7_EfxLvupBG.map.bin.lz"

    .global Tsa8_EfxLvupBG
Tsa8_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa8_EfxLvupBG.map.bin.lz"

    .global Tsa9_EfxLvupBG
Tsa9_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa9_EfxLvupBG.map.bin.lz"

    .global Tsa10_EfxLvupBG
Tsa10_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa10_EfxLvupBG.map.bin.lz"

    .global Tsa11_EfxLvupBG
Tsa11_EfxLvupBG:
    .incbin "graphics/banim/efxlvupfx/Tsa11_EfxLvupBG.map.bin.lz"

	.global Img_EfxLvupBG2
Img_EfxLvupBG2:  @ 0x085C5994
	.incbin "graphics/banim/efxlvupfx/Img_EfxLvupBG2.4bpp.lz"

	.global Pal_EfxLvupBG2
Pal_EfxLvupBG2:  @ 0x085C6054
	.incbin "graphics/banim/efxlvupfx/Pal_EfxLvupBG2.gbapal"

	.global Pal_EfxLvupBGCOL
Pal_EfxLvupBGCOL:  @ 0x085C60D4
	.incbin "graphics/banim/efxlvupfx/Pal_EfxLvupBGCOL.gbapal"

    .global Tsa1_EfxLvupBG2
Tsa1_EfxLvupBG2:
    .incbin "graphics/banim/efxlvupfx/Tsa1_EfxLvupBG2.map.bin.lz"

    .global Tsa2_EfxLvupBG2
Tsa2_EfxLvupBG2:
    .incbin "graphics/banim/efxlvupfx/Tsa2_EfxLvupBG2.map.bin.lz"

    .global Tsa3_EfxLvupBG2
Tsa3_EfxLvupBG2:
    .incbin "graphics/banim/efxlvupfx/Tsa3_EfxLvupBG2.map.bin.lz"

    .global Tsa4_EfxLvupBG2
Tsa4_EfxLvupBG2:
    .incbin "graphics/banim/efxlvupfx/Tsa4_EfxLvupBG2.map.bin.lz"

    .global Tsa5_EfxLvupBG2
Tsa5_EfxLvupBG2:
    .incbin "graphics/banim/efxlvupfx/Tsa5_EfxLvupBG2.map.bin.lz"

    .global Tsa6_EfxLvupBG2
Tsa6_EfxLvupBG2:
    .incbin "graphics/banim/efxlvupfx/Tsa6_EfxLvupBG2.map.bin.lz"

	.global Img_EfxLvupOBJ2
Img_EfxLvupOBJ2:  @ 0x085C6730
	.incbin "graphics/banim/efxlvupfx/Img_EfxLvupOBJ2.4bpp.lz"

	.global AnimSprite_EfxLvupOBJ2_085C69C8
AnimSprite_EfxLvupOBJ2_085C69C8:	@ 0x085C69C8
    ANIM_SPRITE 0x8000, 0x8000, 0x0002, -120, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -104, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -112, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -88, -40
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6A04
AnimSprite_EfxLvupOBJ2_085C6A04:	@ 0x085C6A04
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, -120, -48
    ANIM_SPRITE 0x4000, 0x0000, 0x0004, -120, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -88, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -96, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -72, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -112, -24
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6A58
AnimSprite_EfxLvupOBJ2_085C6A58:	@ 0x085C6A58
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, -104, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -120, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -72, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -80, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -56, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -104, -48
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -96, -24
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6AC4
AnimSprite_EfxLvupOBJ2_085C6AC4:	@ 0x085C6AC4
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, -88, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -104, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x0000, 0x0004, -88, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -56, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -64, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -40, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -112, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -88, -48
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -80, -24
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6B48
AnimSprite_EfxLvupOBJ2_085C6B48:	@ 0x085C6B48
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, -72, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -88, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -40, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -48, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -24, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -96, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -72, -48
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -64, -24
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6BCC
AnimSprite_EfxLvupOBJ2_085C6BCC:	@ 0x085C6BCC
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, -56, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -72, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x0000, 0x0004, -56, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -24, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -32, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -8, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -80, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -56, -48
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -48, -24
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6C5C
AnimSprite_EfxLvupOBJ2_085C6C5C:	@ 0x085C6C5C
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, -40, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -56, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -8, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -16, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 8, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -64, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -32, -24
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -40, -48
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6CEC
AnimSprite_EfxLvupOBJ2_085C6CEC:	@ 0x085C6CEC
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, -24, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -40, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x0000, 0x0004, -24, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 8, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 0, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 24, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -48, -32
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6D70
AnimSprite_EfxLvupOBJ2_085C6D70:	@ 0x085C6D70
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, -8, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -24, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -24, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 24, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 16, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 40, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -32, -32
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6DF4
AnimSprite_EfxLvupOBJ2_085C6DF4:	@ 0x085C6DF4
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, 8, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, -8, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -24, -36
    ANIM_SPRITE 0x4000, 0x0000, 0x0004, 8, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 40, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 32, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 56, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, -16, -32
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6E84
AnimSprite_EfxLvupOBJ2_085C6E84:	@ 0x085C6E84
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, 24, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 8, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -24, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 8, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 56, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 48, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 72, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 0, -32
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6F14
AnimSprite_EfxLvupOBJ2_085C6F14:	@ 0x085C6F14
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, 40, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 24, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -24, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 8, -36
    ANIM_SPRITE 0x4000, 0x0000, 0x0004, 40, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 72, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 64, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 88, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 16, -32
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C6FB0
AnimSprite_EfxLvupOBJ2_085C6FB0:	@ 0x085C6FB0
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, 56, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 40, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -24, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 8, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 40, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 88, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 80, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 104, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 32, -32
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C704C
AnimSprite_EfxLvupOBJ2_085C704C:	@ 0x085C704C
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, 72, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 56, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -24, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 8, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 40, -36
    ANIM_SPRITE 0x4000, 0x0000, 0x0004, 72, -36
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 104, -40
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 96, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 48, -32
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C70E8
AnimSprite_EfxLvupOBJ2_085C70E8:	@ 0x085C70E8
    ANIM_SPRITE 0x0000, 0x8000, 0x0000, 88, -48
    ANIM_SPRITE 0x0000, 0x4000, 0x0024, 72, -40
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -24, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 8, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 40, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 72, -36
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 112, -32
    ANIM_SPRITE 0x0000, 0x0000, 0x0026, 64, -32
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimSprite_EfxLvupOBJ2_085C7178
AnimSprite_EfxLvupOBJ2_085C7178:	@ 0x085C7178
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -120, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -88, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -56, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, -24, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 8, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 40, -36
    ANIM_SPRITE 0x4000, 0x4000, 0x0004, 72, -36
    ANIM_SPRITE 0x4000, 0x0000, 0x0004, 104, -36
    ANIM_SPRITE 0x0001, 0x0000, 0x0000, 0, 0

	.global AnimScr_EfxLvupOBJ2
AnimScr_EfxLvupOBJ2:  @ 0x085C71E4
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C69C8, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6A04, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6A58, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6AC4, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6B48, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6BCC, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6C5C, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6CEC, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6D70, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6DF4, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6E84, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6F14, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C6FB0, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C704C, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C70E8, 1
    ANIMSCR_FORCE_SPRITE AnimSprite_EfxLvupOBJ2_085C7178, 10
    ANIMSCR_BLOCKED
