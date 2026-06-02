    .section .data

	.global Img_SaveMenuBG
Img_SaveMenuBG:  @ 0x08A21658
	.incbin "graphics/misc/Img_SaveMenuBG.4bpp.lz"

	.global Pal_SaveMenuBG
Pal_SaveMenuBG:  @ 0x08A25DCC
	.incbin "graphics/misc/Pal_SaveMenuBG.gbapal"

	.global Tsa_SaveMenuBG
Tsa_SaveMenuBG:  @ 0x08A25ECC
	.incbin "graphics/misc/Tsa_SaveMenuBG.tsa.bin"

	.global Img_MainMenuBgFog
Img_MainMenuBgFog:  @ 0x08A26380
	.incbin "graphics/misc/Img_MainMenuBgFog.4bpp.lz"

	.global Pal_MainMenuBgFog
Pal_MainMenuBgFog:  @ 0x08A268D8
	.incbin "graphics/misc/Pal_MainMenuBgFog.gbapal"

	.global Tsa_MainMenuBgFog
Tsa_MainMenuBgFog:  @ 0x08A268F8
	.incbin "graphics/misc/Tsa_MainMenuBgFog.tsa.bin.lz"

	.global Img_SaveScreenSprits
Img_SaveScreenSprits:  @ 0x08A26A74
	.incbin "graphics/misc/Img_SaveScreenSprits.4bpp.lz"

	.global Pal_SaveScreenSprits
Pal_SaveScreenSprits:  @ 0x08A27F68
	.incbin "graphics/misc/Pal_SaveScreenSprits.gbapal"

	.global Pal_08A28088
Pal_08A28088:  @ 0x08A28088
	.incbin "graphics/misc/Pal_08A28088.gbapal"

	.global gUnknown_08A280A8
gUnknown_08A280A8:  @ 0x08A280A8
gUnknown_08A280A8_motion:
	.2byte (gUnknown_08A280A8_frame_list - gUnknown_08A280A8_motion), (gUnknown_08A280A8_anim_list - gUnknown_08A280A8_motion) @ header

gUnknown_08A280A8_frame_list: @ +$4
	.2byte (gUnknown_08A280A8_frame_0 - gUnknown_08A280A8_frame_list)
	.2byte (gUnknown_08A280A8_frame_1 - gUnknown_08A280A8_frame_list)
	.2byte (gUnknown_08A280A8_frame_2 - gUnknown_08A280A8_frame_list)
	.2byte (gUnknown_08A280A8_frame_3 - gUnknown_08A280A8_frame_list)
	.2byte (gUnknown_08A280A8_frame_4 - gUnknown_08A280A8_frame_list)
	.2byte (gUnknown_08A280A8_frame_5 - gUnknown_08A280A8_frame_list)

gUnknown_08A280A8_anim_list: @ +$10
	.2byte (gUnknown_08A280A8_anim_0 - gUnknown_08A280A8_anim_list)

gUnknown_08A280A8_frame_0: @ +$12
	.2byte 2 @ oam entries
	.2byte 0x40F0, 0x81F0, 0x4 @ OAM Data #0
	.2byte 0x4000, 0x41F0, 0x0 @ OAM Data #1

gUnknown_08A280A8_frame_1: @ +$20
	.2byte 2 @ oam entries
	.2byte 0x4000, 0x41F0, 0x0 @ OAM Data #0
	.2byte 0x40F0, 0x81F0, 0x8 @ OAM Data #1

gUnknown_08A280A8_frame_2: @ +$2E
	.2byte 2 @ oam entries
	.2byte 0x4000, 0x41F0, 0x0 @ OAM Data #0
	.2byte 0x40F0, 0x81F0, 0xC @ OAM Data #1

gUnknown_08A280A8_frame_3: @ +$3C
	.2byte 2 @ oam entries
	.2byte 0x4000, 0x41F0, 0x0 @ OAM Data #0
	.2byte 0x40F0, 0x81F0, 0x10 @ OAM Data #1

gUnknown_08A280A8_frame_4: @ +$4A
	.2byte 2 @ oam entries
	.2byte 0x4000, 0x41F0, 0x0 @ OAM Data #0
	.2byte 0x40F0, 0x81F0, 0x14 @ OAM Data #1

gUnknown_08A280A8_frame_5: @ +$58
	.2byte 2 @ oam entries
	.2byte 0x4000, 0x41F0, 0x0 @ OAM Data #0
	.2byte 0x40F0, 0x81F0, 0x18 @ OAM Data #1

gUnknown_08A280A8_anim_0: @ +$66
	.2byte  8,  0
	.2byte  9,  1
	.2byte 10,  2
	.2byte 15,  3
	.2byte 10,  4
	.2byte 60,  5

	.2byte 0, (-1) @ loop back to start

	.byte 0x00, 0x00  @ trailing anim data not decoded by apdump
.L_end_gUnknown_08A280A8:
	.if (.L_end_gUnknown_08A280A8 - gUnknown_08A280A8) != 132
	.error "gUnknown_08A280A8 size mismatch"
	.endif

	.global Img_GameMainMenuObjs
Img_GameMainMenuObjs:  @ 0x08A2812C
	.incbin "graphics/misc/Img_GameMainMenuObjs.4bpp.lz"

	.global Img_DifficultyMenuObjs
Img_DifficultyMenuObjs:  @ 0x08A28A0C
	.incbin "graphics/misc/Img_DifficultyMenuObjs.4bpp.lz"

	.global Pal_DifficultyMenuObjs
Pal_DifficultyMenuObjs:  @ 0x08A29418
	.incbin "graphics/misc/Pal_DifficultyMenuObjs.gbapal"

	.global gUnknown_08A29498
gUnknown_08A29498:  @ 0x08A29498
	.incbin "graphics/misc/gUnknown_08A29498.4bpp"

	.global gUnknown_08A29558
gUnknown_08A29558:  @ 0x08A29558
	.incbin "graphics/misc/gUnknown_08A29558.tsa.bin.lz"

	.global Pal_08A295B4
Pal_08A295B4:  @ 0x08A295B4
	.incbin "graphics/misc/Pal_08A295B4.gbapal"

	.global Tsa_CommGameBgScreenInShop
Tsa_CommGameBgScreenInShop:  @ 0x08A295D4
	.incbin "graphics/misc/Tsa_CommGameBgScreenInShop.tsa.bin"

	.global gUnknown_08A29A88
gUnknown_08A29A88:  @ 0x08A29A88
	.incbin "graphics/misc/gUnknown_08A29A88.4bpp.lz"

	.global gUnknown_08A2B1E4
gUnknown_08A2B1E4:  @ 0x08A2B1E4
	.incbin "graphics/misc/gUnknown_08A2B1E4.4bpp.lz"

	.global gUnknown_08A2C11C
gUnknown_08A2C11C:  @ 0x08A2C11C
	.incbin "graphics/misc/gUnknown_08A2C11C.4bpp"

	.global gUnknown_08A2C23C
gUnknown_08A2C23C:  @ 0x08A2C23C
	@ RGB15 color/palette table (326 u16 entries, 652 bytes)
	.2byte 0x7FFF, 0x739C, 0x6319, 0x52B6, 0x4233, 0x31CF, 0x254C, 0x14E9
	.2byte 0x14E9, 0x214C, 0x31CF, 0x4232, 0x52B6, 0x6319, 0x739C, 0x7FFF
	.2byte 0x0000, 0x0000, 0x6F04, 0x5A22, 0x59E1, 0x5E62, 0x6263, 0x6EA2
	.2byte 0x7FDD, 0x31F2, 0x7FFF, 0x4AB9, 0x5F7D, 0x63BD, 0x6FFF, 0x77FF
	.2byte 0x0004, 0x001E, 0x001E, 0x004A, 0x0076, 0x00A2, 0x00CE, 0x00FA
	.2byte 0x0126, 0x0152, 0x017E, 0x01AA, 0x01D6, 0x01F0, 0x01FE, 0x01F2
	.2byte 0x021A, 0x0007, 0x00EC, 0x0003, 0x7001, 0x40F4, 0x01FB, 0x7002
	.2byte 0x00FC, 0x01FB, 0x7000, 0x40F8, 0x01F8, 0x7004, 0x00F8, 0x0008
	.2byte 0x7006, 0x4000, 0x01F8, 0x7007, 0x0000, 0x0008, 0x7009, 0x0007
	.2byte 0x00EB, 0x0003, 0x7001, 0x40F3, 0x01FB, 0x7002, 0x00FB, 0x01FB
	.2byte 0x7000, 0x4000, 0x01F8, 0x7007, 0x0000, 0x0008, 0x7009, 0x40F8
	.2byte 0x01F8, 0x700A, 0x00F8, 0x0008, 0x700C, 0x0007, 0x00EB, 0x0004
	.2byte 0x7001, 0x40F3, 0x01FC, 0x7002, 0x00FB, 0x01FC, 0x7000, 0x4000
	.2byte 0x01F8, 0x7007, 0x0000, 0x0008, 0x7009, 0x40F8, 0x01F8, 0x700A
	.2byte 0x00F8, 0x0008, 0x700C, 0x0007, 0x00EB, 0x0005, 0x7001, 0x40F3
	.2byte 0x01FD, 0x7002, 0x00FB, 0x01FD, 0x7000, 0x40F8, 0x01F8, 0x700D
	.2byte 0x00F8, 0x0008, 0x700F, 0x4000, 0x01F8, 0x7007, 0x0000, 0x0008
	.2byte 0x7009, 0x0007, 0x00EB, 0x0006, 0x7001, 0x40F3, 0x01FE, 0x7002
	.2byte 0x00FB, 0x01FE, 0x7000, 0x40F8, 0x01F8, 0x700D, 0x00F8, 0x0008
	.2byte 0x700F, 0x4000, 0x01F8, 0x7007, 0x0000, 0x0008, 0x7009, 0x0007
	.2byte 0x00EB, 0x0007, 0x7001, 0x40F3, 0x01FF, 0x7002, 0x00FB, 0x01FF
	.2byte 0x7000, 0x4000, 0x01F8, 0x7007, 0x0000, 0x0008, 0x7009, 0x40F8
	.2byte 0x01F8, 0x7010, 0x00F8, 0x0008, 0x7012, 0x0007, 0x00EA, 0x0007
	.2byte 0x7001, 0x40F2, 0x01FF, 0x7002, 0x00FA, 0x01FF, 0x7000, 0x4000
	.2byte 0x01F8, 0x7007, 0x0000, 0x0008, 0x7009, 0x40F8, 0x01F8, 0x7010
	.2byte 0x00F8, 0x0008, 0x7012, 0x0007, 0x00E9, 0x0008, 0x7001, 0x40F1
	.2byte 0x0000, 0x7002, 0x00F9, 0x0000, 0x7000, 0x4000, 0x01F8, 0x7007
	.2byte 0x0000, 0x0008, 0x7009, 0x40F8, 0x01F8, 0x7010, 0x00F8, 0x0008
	.2byte 0x7012, 0x0007, 0x00E8, 0x0008, 0x7001, 0x40F0, 0x0000, 0x7002
	.2byte 0x00F8, 0x0000, 0x7000, 0x4000, 0x01F8, 0x7007, 0x0000, 0x0008
	.2byte 0x7009, 0x40F8, 0x01F8, 0x7010, 0x00F8, 0x0008, 0x7012, 0x0007
	.2byte 0x00EC, 0x0006, 0x7001, 0x40F4, 0x01FE, 0x7002, 0x00FC, 0x01FE
	.2byte 0x7000, 0x40F8, 0x01F8, 0x7004, 0x00F8, 0x0008, 0x7006, 0x4000
	.2byte 0x01F8, 0x7007, 0x0000, 0x0008, 0x7009, 0x0004, 0x40F7, 0x01F7
	.2byte 0x7013, 0x00F7, 0x0007, 0x7015, 0x40FF, 0x01FE, 0x7016, 0x00EF
	.2byte 0x0006, 0x7018, 0x0002, 0x40F3, 0x01FC, 0x7019, 0x40FB, 0x01FC
	.2byte 0x701B, 0x0002, 0x40FA, 0x01FE, 0x701D, 0x00F2, 0x0005, 0x701F
	.2byte 0x0007, 0x0000, 0x0007, 0x0001, 0x0008, 0x0002, 0x000A, 0x0003
	.2byte 0x0006, 0x0004, 0x0005, 0x0005, 0x0006, 0x0006, 0x0005, 0x0007
	.2byte 0x001E, 0x0008, 0x0000, 0xFFFF, 0x0004, 0x0009, 0x0004, 0x000A
	.2byte 0x0004, 0x000B, 0x0004, 0x000C, 0x0000, 0xFFFF
.L_end_gUnknown_08A2C23C:
	.if (.L_end_gUnknown_08A2C23C - gUnknown_08A2C23C) != 652
	.error "gUnknown_08A2C23C size mismatch"
	.endif

	.global gUnknown_08A2C4C8
gUnknown_08A2C4C8:  @ 0x08A2C4C8
	.incbin "graphics/misc/gUnknown_08A2C4C8.4bpp"

	.global gUnknown_08A2C5A8
gUnknown_08A2C5A8:  @ 0x08A2C5A8
	.incbin "graphics/misc/gUnknown_08A2C5A8.tsa.bin"

	.global gUnknown_08A2C7A4
gUnknown_08A2C7A4:  @ 0x08A2C7A4
	.incbin "graphics/misc/gUnknown_08A2C7A4.tsa.bin"

	.global Img_SoundRoomVolumeGraph
Img_SoundRoomVolumeGraph:  @ 0x08A2C838
	.incbin "graphics/misc/Img_SoundRoomVolumeGraph.4bpp.lz"

	.global Pal_SoundRoomVolumeGraph
Pal_SoundRoomVolumeGraph:  @ 0x08A2C8A8
	.incbin "graphics/misc/Pal_SoundRoomVolumeGraph.gbapal"

	.global gUnknown_08A2C908
gUnknown_08A2C908:  @ 0x08A2C908
	.incbin "graphics/misc/gUnknown_08A2C908.4bpp.lz"

	.global gUnknown_08A2C92C
gUnknown_08A2C92C:  @ 0x08A2C92C
	.incbin "graphics/misc/gUnknown_08A2C92C.map.bin"

	.global Img_SoundRoomUiElements
Img_SoundRoomUiElements:  @ 0x08A2CABC
	.incbin "graphics/misc/Img_SoundRoomUiElements.4bpp.lz"

	.global Pal_SoundRoomUiElements
Pal_SoundRoomUiElements:  @ 0x08A2D2CC
	.incbin "graphics/misc/Pal_SoundRoomUiElements.gbapal"

	.global Img_PlayStatusSprites
Img_PlayStatusSprites:  @ 0x08A2D32C
	.incbin "graphics/misc/Img_PlayStatusSprites.4bpp.lz"

	.global Pal_PlayStatusSprites
Pal_PlayStatusSprites:  @ 0x08A2E1B8
	.incbin "graphics/misc/Pal_PlayStatusSprites.gbapal"

	.global Img_ChapterStatusSelectorSprite
Img_ChapterStatusSelectorSprite:  @ 0x08A2E1F8
	.incbin "graphics/misc/Img_ChapterStatusSelectorSprite.4bpp.lz"

	.global Img_StatusScreenLabelSprites
Img_StatusScreenLabelSprites:  @ 0x08A2E214
	.incbin "graphics/misc/Img_StatusScreenLabelSprites.4bpp.lz"

	.global Pal_StatusScreenLabelSprites
Pal_StatusScreenLabelSprites:  @ 0x08A2E4A4
	.incbin "graphics/misc/Pal_StatusScreenLabelSprites.gbapal"

	.global Tsa_ChapterStatusUi
Tsa_ChapterStatusUi:  @ 0x08A2E4C4
	.incbin "graphics/misc/Tsa_ChapterStatusUi.tsa.bin.lz"

	.global Img_08A2E5EC
Img_08A2E5EC:  @ 0x08A2E5EC
	.incbin "graphics/misc/Img_08A2E5EC.4bpp.lz"

	.global gUnknown_08A2E890
gUnknown_08A2E890:  @ 0x08A2E890
	.incbin "graphics/misc/gUnknown_08A2E890.gbapal"

	.global Pal_08A2E8F0
Pal_08A2E8F0:  @ 0x08A2E8F0
	.incbin "graphics/misc/Pal_08A2E8F0.gbapal"

	.global Img_SysBlackBox
Img_SysBlackBox:  @ 0x08A2E950
	.incbin "graphics/misc/Img_SysBlackBox.4bpp.lz"
