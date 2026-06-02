.section .data

	.global Obj_PoisonAnim
Obj_PoisonAnim:  @ 0x089A6F40
Obj_PoisonAnim_motion:
	.2byte (Obj_PoisonAnim_frame_list - Obj_PoisonAnim_motion), (Obj_PoisonAnim_anim_list - Obj_PoisonAnim_motion) @ header

Obj_PoisonAnim_frame_list: @ +$4
	.2byte (Obj_PoisonAnim_frame_0 - Obj_PoisonAnim_frame_list)
	.2byte (Obj_PoisonAnim_frame_1 - Obj_PoisonAnim_frame_list)
	.2byte (Obj_PoisonAnim_frame_2 - Obj_PoisonAnim_frame_list)
	.2byte (Obj_PoisonAnim_frame_3 - Obj_PoisonAnim_frame_list)
	.2byte (Obj_PoisonAnim_frame_4 - Obj_PoisonAnim_frame_list)
	.2byte (Obj_PoisonAnim_frame_5 - Obj_PoisonAnim_frame_list)
	.2byte (Obj_PoisonAnim_frame_6 - Obj_PoisonAnim_frame_list)
	.2byte (Obj_PoisonAnim_frame_7 - Obj_PoisonAnim_frame_list)
	.2byte (Obj_PoisonAnim_frame_8 - Obj_PoisonAnim_frame_list)

Obj_PoisonAnim_anim_list: @ +$16
	.2byte (Obj_PoisonAnim_anim_0 - Obj_PoisonAnim_anim_list)

Obj_PoisonAnim_frame_0: @ +$18
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0x0 @ OAM Data #0

Obj_PoisonAnim_frame_1: @ +$20
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0x2 @ OAM Data #0

Obj_PoisonAnim_frame_2: @ +$28
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0x4 @ OAM Data #0

Obj_PoisonAnim_frame_3: @ +$30
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0x6 @ OAM Data #0

Obj_PoisonAnim_frame_4: @ +$38
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0x8 @ OAM Data #0

Obj_PoisonAnim_frame_5: @ +$40
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0xA @ OAM Data #0

Obj_PoisonAnim_frame_6: @ +$48
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0xC @ OAM Data #0

Obj_PoisonAnim_frame_7: @ +$50
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0xE @ OAM Data #0

Obj_PoisonAnim_frame_8: @ +$58
	.2byte 1 @ oam entries
	.2byte 0xF0, 0x4000, 0x10 @ OAM Data #0

Obj_PoisonAnim_anim_0: @ +$60
	.2byte  7,  0
	.2byte  7,  1
	.2byte  7,  2
	.2byte  7,  0
	.2byte  7,  1
	.2byte  7,  2
	.2byte  6,  3
	.2byte  6,  4
	.2byte  5,  5
	.2byte  5,  6
	.2byte  5,  7
	.2byte  4,  8

	.2byte 0, 1 @ kill animated object

	.byte 0x00, 0x00, 0xFF, 0xFF  @ trailing anim data not decoded by apdump
.L_end_Obj_PoisonAnim:
	.if (.L_end_Obj_PoisonAnim - Obj_PoisonAnim) != 152
	.error "Obj_PoisonAnim size mismatch"
	.endif

	.global Obj_WallBreakAnim
Obj_WallBreakAnim:  @ 0x089A6FD8
	.incbin "graphics/misc/Obj_WallBreakAnim.4bpp"

	.global ApHandle_GmapSoguSprites
ApHandle_GmapSoguSprites:  @ 0x089A8EF8
ApHandle_GmapSoguSprites_motion:
	.2byte (ApHandle_GmapSoguSprites_frame_list - ApHandle_GmapSoguSprites_motion), (ApHandle_GmapSoguSprites_anim_list - ApHandle_GmapSoguSprites_motion) @ header

ApHandle_GmapSoguSprites_frame_list: @ +$4
	.2byte (ApHandle_GmapSoguSprites_frame_0 - ApHandle_GmapSoguSprites_frame_list)
	.2byte (ApHandle_GmapSoguSprites_frame_1 - ApHandle_GmapSoguSprites_frame_list)
	.2byte (ApHandle_GmapSoguSprites_frame_2 - ApHandle_GmapSoguSprites_frame_list)
	.2byte (ApHandle_GmapSoguSprites_frame_3 - ApHandle_GmapSoguSprites_frame_list)
	.2byte (ApHandle_GmapSoguSprites_frame_4 - ApHandle_GmapSoguSprites_frame_list)

ApHandle_GmapSoguSprites_anim_list: @ +$E
	.2byte (ApHandle_GmapSoguSprites_anim_0 - ApHandle_GmapSoguSprites_anim_list)

ApHandle_GmapSoguSprites_frame_0: @ +$10
	.2byte 1 @ oam entries
	.2byte 0xF8, 0x41F8, 0x0 @ OAM Data #0

ApHandle_GmapSoguSprites_frame_1: @ +$18
	.2byte 1 @ oam entries
	.2byte 0xFB, 0x41F8, 0x0 @ OAM Data #0

ApHandle_GmapSoguSprites_frame_2: @ +$20
	.2byte 1 @ oam entries
	.2byte 0xFA, 0x41F8, 0x0 @ OAM Data #0

ApHandle_GmapSoguSprites_frame_3: @ +$28
	.2byte 1 @ oam entries
	.2byte 0xF9, 0x41F8, 0x0 @ OAM Data #0

ApHandle_GmapSoguSprites_frame_4: @ +$30
	.2byte 1 @ oam entries
	.2byte 0xF8, 0x41F8, 0x0 @ OAM Data #0

ApHandle_GmapSoguSprites_anim_0: @ +$38
	.2byte  4,  0
	.2byte  2,  1
	.2byte  2,  0
	.2byte  2,  2
	.2byte  2,  0
	.2byte  2,  3
	.2byte 30,  0

	.2byte 0, 1 @ kill animated object

	.byte 0x00, 0x00, 0xFF, 0xFF, 0x13, 0x4F, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3C, 0x0D, 0x9E, 0x1A, 0xDF, 0x1B, 0x0B, 0x04, 0x12, 0x00, 0x97, 0x10, 0x3C, 0x0D, 0x9E, 0x1A, 0xDF, 0x1B, 0x30, 0x3A, 0xFF, 0x7F, 0x00, 0x00  @ trailing anim data not decoded by apdump
.L_end_ApHandle_GmapSoguSprites:
	.if (.L_end_ApHandle_GmapSoguSprites - ApHandle_GmapSoguSprites) != 124
	.error "ApHandle_GmapSoguSprites size mismatch"
	.endif

	.global Pal_MapAnimManaketeMu
Pal_MapAnimManaketeMu:  @ 0x089A8F74
	.incbin "graphics/misc/Pal_MapAnimManaketeMu.gbapal"

	.global gGfx_ArenaBuildingFront
gGfx_ArenaBuildingFront:  @ 0x089A8F94
	.incbin "graphics/misc/gGfx_ArenaBuildingFront.4bpp.lz"

	.global gTsa_ArenaBuildingFront
gTsa_ArenaBuildingFront:  @ 0x089ABB70
	.incbin "graphics/misc/gTsa_ArenaBuildingFront.tsa.bin"

	.global gPal_ArenaBuildingFront
gPal_ArenaBuildingFront:  @ 0x089AC024
	.incbin "graphics/misc/gPal_ArenaBuildingFront.gbapal"

	.global Img_MapAnimMISS
Img_MapAnimMISS:  @ 0x089AC0A4
	.incbin "graphics/misc/Img_MapAnimMISS.4bpp.lz"

	.global Obj_MapAnimMISS
Obj_MapAnimMISS:  @ 0x089AC194
Obj_MapAnimMISS_motion:
	.2byte (Obj_MapAnimMISS_frame_list - Obj_MapAnimMISS_motion), (Obj_MapAnimMISS_anim_list - Obj_MapAnimMISS_motion) @ header

Obj_MapAnimMISS_frame_list: @ +$4
	.2byte (Obj_MapAnimMISS_frame_0 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_1 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_2 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_3 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_4 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_5 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_6 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_7 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_8 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_9 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_10 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_11 - Obj_MapAnimMISS_frame_list)
	.2byte (Obj_MapAnimMISS_frame_12 - Obj_MapAnimMISS_frame_list)

Obj_MapAnimMISS_anim_list: @ +$1E
	.2byte (Obj_MapAnimMISS_anim_0 - Obj_MapAnimMISS_anim_list)

Obj_MapAnimMISS_frame_0: @ +$20
	.2byte 1 @ oam entries
	.2byte 0x40F0, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_1: @ +$28
	.2byte 2 @ oam entries
	.2byte 0x40F0, 0x41EE, 0x4 @ OAM Data #0
	.2byte 0xF0, 0xE, 0x8 @ OAM Data #1

Obj_MapAnimMISS_frame_2: @ +$36
	.2byte 12 @ oam entries
	.2byte 0xF0, 0x1F1, 0x9 @ OAM Data #0
	.2byte 0xF0, 0x1F1, 0x9 @ OAM Data #1
	.2byte 0xF0, 0x1F1, 0x9 @ OAM Data #2
	.2byte 0xF0, 0x1F9, 0x9 @ OAM Data #3
	.2byte 0xF0, 0x1, 0x9 @ OAM Data #4
	.2byte 0xF0, 0x1, 0x9 @ OAM Data #5
	.2byte 0xF0, 0x1, 0x9 @ OAM Data #6
	.2byte 0xF0, 0x9, 0x9 @ OAM Data #7
	.2byte 0xF0, 0x11, 0x9 @ OAM Data #8
	.2byte 0xF0, 0x1E9, 0x9 @ OAM Data #9
	.2byte 0xF0, 0x1E9, 0x9 @ OAM Data #10
	.2byte 0xF0, 0x1E9, 0x9 @ OAM Data #11

Obj_MapAnimMISS_frame_3: @ +$80
	.2byte 4 @ oam entries
	.2byte 0x40F0, 0x1E1, 0xA @ OAM Data #0
	.2byte 0x40F0, 0x1F1, 0xA @ OAM Data #1
	.2byte 0x40F0, 0x1, 0xA @ OAM Data #2
	.2byte 0x40F0, 0x11, 0xA @ OAM Data #3

Obj_MapAnimMISS_frame_4: @ +$9A
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x19, 0x19
	.2byte 1 @ oam entries
	.2byte 0x41F9, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_5: @ +$AA
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x33, 0x33
	.2byte 1 @ oam entries
	.2byte 0x41F8, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_6: @ +$BA
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x4C, 0x4C
	.2byte 1 @ oam entries
	.2byte 0x41F7, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_7: @ +$CA
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x66, 0x66
	.2byte 1 @ oam entries
	.2byte 0x41F6, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_8: @ +$DA
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x80, 0x80
	.2byte 1 @ oam entries
	.2byte 0x41F5, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_9: @ +$EA
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x99, 0x99
	.2byte 1 @ oam entries
	.2byte 0x41F4, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_10: @ +$FA
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0xB3, 0xB3
	.2byte 1 @ oam entries
	.2byte 0x41F3, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_11: @ +$10A
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0xCC, 0xCC
	.2byte 1 @ oam entries
	.2byte 0x41F2, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_frame_12: @ +$11A
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0xE6, 0xE6
	.2byte 1 @ oam entries
	.2byte 0x41F1, 0x41F1, 0x0 @ OAM Data #0

Obj_MapAnimMISS_anim_0: @ +$12A
	.2byte  1,  4
	.2byte  1,  5
	.2byte  1,  6
	.2byte  1,  7
	.2byte  1,  8
	.2byte  1,  9
	.2byte  1, 10
	.2byte  1, 11
	.2byte  1, 12
	.2byte 10,  0
	.2byte  3,  1
	.2byte  3,  2
	.2byte  3,  3

	.2byte 0, 1 @ kill animated object

	.byte 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00  @ trailing anim data not decoded by apdump
.L_end_Obj_MapAnimMISS:
	.if (.L_end_Obj_MapAnimMISS - Obj_MapAnimMISS) != 360
	.error "Obj_MapAnimMISS size mismatch"
	.endif

	.global Img_MapAnimNODAMAGE
Img_MapAnimNODAMAGE:  @ 0x089AC2FC
	.incbin "graphics/misc/Img_MapAnimNODAMAGE.4bpp.lz"

	.global obj_MapAnimNODAMAGE
obj_MapAnimNODAMAGE:  @ 0x089AC440
obj_MapAnimNODAMAGE_motion:
	.2byte (obj_MapAnimNODAMAGE_frame_list - obj_MapAnimNODAMAGE_motion), (obj_MapAnimNODAMAGE_anim_list - obj_MapAnimNODAMAGE_motion) @ header

obj_MapAnimNODAMAGE_frame_list: @ +$4
	.2byte (obj_MapAnimNODAMAGE_frame_0 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_1 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_2 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_3 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_4 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_5 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_6 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_7 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_8 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_9 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_10 - obj_MapAnimNODAMAGE_frame_list)
	.2byte (obj_MapAnimNODAMAGE_frame_11 - obj_MapAnimNODAMAGE_frame_list)

obj_MapAnimNODAMAGE_anim_list: @ +$1C
	.2byte (obj_MapAnimNODAMAGE_anim_0 - obj_MapAnimNODAMAGE_anim_list)

obj_MapAnimNODAMAGE_frame_0: @ +$1E
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x33, 0x33
	.2byte 2 @ oam entries
	.2byte 0x41F8, 0x41ED, 0x0 @ OAM Data #0
	.2byte 0x41F8, 0x1FA, 0x4 @ OAM Data #1

obj_MapAnimNODAMAGE_frame_1: @ +$34
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x4C, 0x4C
	.2byte 3 @ oam entries
	.2byte 0x41F7, 0x41EA, 0x0 @ OAM Data #0
	.2byte 0x41F7, 0x1F9, 0x4 @ OAM Data #1
	.2byte 0x1F7, 0x1, 0x6 @ OAM Data #2

obj_MapAnimNODAMAGE_frame_2: @ +$50
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x66, 0x66
	.2byte 3 @ oam entries
	.2byte 0x41F6, 0x41E9, 0x0 @ OAM Data #0
	.2byte 0x41F6, 0x1FB, 0x4 @ OAM Data #1
	.2byte 0x1F6, 0x4, 0x6 @ OAM Data #2

obj_MapAnimNODAMAGE_frame_3: @ +$6C
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x80, 0x80
	.2byte 3 @ oam entries
	.2byte 0x41F5, 0x41E7, 0x0 @ OAM Data #0
	.2byte 0x41F5, 0x1FB, 0x4 @ OAM Data #1
	.2byte 0x1F5, 0x5, 0x6 @ OAM Data #2

obj_MapAnimNODAMAGE_frame_4: @ +$88
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x99, 0x99
	.2byte 3 @ oam entries
	.2byte 0x41F4, 0x41E6, 0x0 @ OAM Data #0
	.2byte 0x41F4, 0x1FD, 0x4 @ OAM Data #1
	.2byte 0x1F4, 0x8, 0x6 @ OAM Data #2

obj_MapAnimNODAMAGE_frame_5: @ +$A4
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0xB3, 0xB3
	.2byte 3 @ oam entries
	.2byte 0x41F3, 0x41E5, 0x0 @ OAM Data #0
	.2byte 0x41F3, 0x1FF, 0x4 @ OAM Data #1
	.2byte 0x1F3, 0xC, 0x6 @ OAM Data #2

obj_MapAnimNODAMAGE_frame_6: @ +$C0
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0xCC, 0xCC
	.2byte 3 @ oam entries
	.2byte 0x41F2, 0x41E6, 0x0 @ OAM Data #0
	.2byte 0x41F2, 0x2, 0x4 @ OAM Data #1
	.2byte 0x1F2, 0x10, 0x6 @ OAM Data #2

obj_MapAnimNODAMAGE_frame_7: @ +$DC
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0xE6, 0xE6
	.2byte 3 @ oam entries
	.2byte 0x41F1, 0x41E5, 0x0 @ OAM Data #0
	.2byte 0x41F1, 0x4, 0x4 @ OAM Data #1
	.2byte 0x1F1, 0x13, 0x6 @ OAM Data #2

obj_MapAnimNODAMAGE_frame_8: @ +$F8
	.2byte 3 @ oam entries
	.2byte 0x40F0, 0x41E4, 0x0 @ OAM Data #0
	.2byte 0x40F0, 0x4, 0x4 @ OAM Data #1
	.2byte 0xF0, 0x14, 0x6 @ OAM Data #2

obj_MapAnimNODAMAGE_frame_9: @ +$10C
	.2byte 2 @ oam entries
	.2byte 0x40F0, 0x41DE, 0x7 @ OAM Data #0
	.2byte 0x40F0, 0x41FE, 0xB @ OAM Data #1

obj_MapAnimNODAMAGE_frame_10: @ +$11A
	.2byte 4 @ oam entries
	.2byte 0x40F0, 0x1DE, 0xF @ OAM Data #0
	.2byte 0x40F0, 0x1EE, 0xF @ OAM Data #1
	.2byte 0x40F0, 0x1FE, 0xF @ OAM Data #2
	.2byte 0x40F0, 0xE, 0xF @ OAM Data #3

obj_MapAnimNODAMAGE_frame_11: @ +$134
	.2byte 5 @ oam entries
	.2byte 0x40F0, 0x1D6, 0x11 @ OAM Data #0
	.2byte 0x40F0, 0x1E6, 0x11 @ OAM Data #1
	.2byte 0x40F0, 0x1F6, 0x11 @ OAM Data #2
	.2byte 0x40F0, 0x6, 0x11 @ OAM Data #3
	.2byte 0x40F0, 0x16, 0x11 @ OAM Data #4

obj_MapAnimNODAMAGE_anim_0: @ +$154
	.2byte  1,  0
	.2byte  1,  1
	.2byte  1,  2
	.2byte  1,  3
	.2byte  1,  4
	.2byte  1,  5
	.2byte  1,  6
	.2byte  1,  7
	.2byte 10,  8
	.2byte  3,  9
	.2byte  3, 10
	.2byte  3, 11

	.2byte 0, 1 @ kill animated object

	.byte 0x00, 0x00, 0xFF, 0xFF  @ trailing anim data not decoded by apdump
.L_end_obj_MapAnimNODAMAGE:
	.if (.L_end_obj_MapAnimNODAMAGE - obj_MapAnimNODAMAGE) != 396
	.error "obj_MapAnimNODAMAGE size mismatch"
	.endif
