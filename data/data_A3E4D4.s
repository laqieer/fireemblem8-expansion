    .section .data

@ Unused/leftover Character Ending Menu background data (not referenced by any
@ symbol or pointer). Splits into a 30x20 BG tilemap whose tiles are based at
@ char 0x260 -- where Img_CharacterEndingMenu is decompressed (ending_details.c)
@ -- followed by a 2x16 RGB15 palette.
gUnknown_08A3F21C:  @ 0x08A3F21C
	.incbin "graphics/misc/gUnknown_08A3F21C.map.bin"
@ 2x16 RGB15 palette (32 colors). All colors have bit 15 clear, so it is stored
@ as a JASC .pal (gbagfx regenerates the .gbapal byte-identically).
	.global Pal_08A3F6D0
Pal_08A3F6D0:  @ 0x08A3F6D0
	.incbin "graphics/misc/Pal_08A3F6D0.gbapal"

	.global Pal_CharacterEndingMenu
Pal_CharacterEndingMenu:  @ 0x08A3F710
	.incbin "graphics/misc/Pal_CharacterEndingMenu.gbapal"

	.global Img_CharacterEndingMenu
Img_CharacterEndingMenu:  @ 0x08A3F750
	.incbin "graphics/misc/Img_CharacterEndingMenu.4bpp.lz"

	.global Tsa_CharacterEnding_TopBorder
Tsa_CharacterEnding_TopBorder:  @ 0x08A3FFEC
    .incbin "graphics/misc/Tsa_CharacterEnding_TopBorder.map.bin"

	.global Tsa_CharacterEnding_BottomBorder
Tsa_CharacterEnding_BottomBorder:  @ 0x08A40068
	.incbin "graphics/misc/Tsa_CharacterEnding_BottomBorder.map.bin"

	.global gTsa_SoloEndingNameplate
gTsa_SoloEndingNameplate:  @ 0x08A400E4
	.incbin "graphics/misc/gTsa_SoloEndingNameplate.map.bin.lz"

	.global gTsa_SoloEndingWindow
gTsa_SoloEndingWindow:  @ 0x08A40204
	.incbin "graphics/misc/gTsa_SoloEndingWindow.map.bin.lz"

	.global gTsa_PairedEndingNameplates
gTsa_PairedEndingNameplates:  @ 0x08A4034C
	.incbin "graphics/misc/gTsa_PairedEndingNameplates.map.bin.lz"

	.global gTsa_PairedEndingWindow
gTsa_PairedEndingWindow:  @ 0x08A40470
	.incbin "graphics/misc/gTsa_PairedEndingWindow.map.bin.lz"

	.global Pal_FinScreen
Pal_FinScreen:  @ 0x08A405B4
	.incbin "graphics/misc/Pal_FinScreen.gbapal"

	.global Img_FinScreen
Img_FinScreen:  @ 0x08A405D4
	.incbin "graphics/misc/Img_FinScreen.4bpp.lz"

	.global Tsa_FinScreen
Tsa_FinScreen:  @ 0x08A409D0
	.incbin "graphics/misc/Tsa_FinScreen.map.bin.lz"

	.global Pal_08A40AD4
Pal_08A40AD4:  @ 0x08A40AD4
    .incbin "graphics/misc/Pal_08A40AD4.gbapal"

	.global Tsa_08A40B14
Tsa_08A40B14:  @ 0x08A40B14
	.incbin "graphics/misc/Tsa_08A40B14.map.bin"

	.global Pal_StaffReelEnt_08A40FC8
Pal_StaffReelEnt_08A40FC8:  @ 0x08A40FC8
	.incbin "graphics/misc/Pal_StaffReelEnt_08A40FC8.gbapal"@ 0xA40FE8 - 0xA40FC8

	.global Img_StaffReelEnt_08A40FE8
Img_StaffReelEnt_08A40FE8:  @ 0x08A40FE8
	.incbin "graphics/misc/Img_StaffReelEnt_08A40FE8.4bpp.lz"@ 0xA41B30 - 0xA40FE8

	.global Img_StaffReelEnt_08A41B30
Img_StaffReelEnt_08A41B30:  @ 0x08A41B30
	.incbin "graphics/misc/Img_StaffReelEnt_08A41B30.4bpp.lz"@ 0xA42748 - 0xA41B30

	.global Img_StaffReelEnt_08A42748
Img_StaffReelEnt_08A42748:  @ 0x08A42748
	.incbin "graphics/misc/Img_StaffReelEnt_08A42748.4bpp.lz"@ 0xA432C0 - 0xA42748

	.global Img_StaffReelEnt_08A432C0
Img_StaffReelEnt_08A432C0:  @ 0x08A432C0
	.incbin "graphics/misc/Img_StaffReelEnt_08A432C0.4bpp.lz"@ 0xA43CBC - 0xA432C0

	.global Img_StaffReelEnt_08A43CBC
Img_StaffReelEnt_08A43CBC:  @ 0x08A43CBC
	.incbin "graphics/misc/Img_StaffReelEnt_08A43CBC.4bpp.lz"@ 0xA45150 - 0xA43CBC

	.global Img_StaffReelEnt_08A45150
Img_StaffReelEnt_08A45150:  @ 0x08A45150
	.incbin "graphics/misc/Img_StaffReelEnt_08A45150.4bpp.lz"@ 0xA4561C - 0xA45150

	.global Img_StaffReelEnt_08A4561C
Img_StaffReelEnt_08A4561C:  @ 0x08A4561C
	.incbin "graphics/misc/Img_StaffReelEnt_08A4561C.4bpp.lz"@ 0xA45F58 - 0xA4561C

	.global Img_StaffReelEnt_08A45F58
Img_StaffReelEnt_08A45F58:  @ 0x08A45F58
	.incbin "graphics/misc/Img_StaffReelEnt_08A45F58.4bpp.lz"@ 0xA46988 - 0xA45F58

	.global Img_StaffReelEnt_08A46988
Img_StaffReelEnt_08A46988:  @ 0x08A46988
	.incbin "graphics/misc/Img_StaffReelEnt_08A46988.4bpp.lz"@ 0xA472B0 - 0xA46988

	.global Img_StaffReelEnt_08A472B0
Img_StaffReelEnt_08A472B0:  @ 0x08A472B0
	.incbin "graphics/misc/Img_StaffReelEnt_08A472B0.4bpp.lz"@ 0xA48744 - 0xA472B0

	.global Img_StaffReelEnt_08A48744
Img_StaffReelEnt_08A48744:  @ 0x08A48744
	.incbin "graphics/misc/Img_StaffReelEnt_08A48744.4bpp.lz"@ 0xA497A8 - 0xA48744

	.global Img_StaffReelEnt_08A497A8
Img_StaffReelEnt_08A497A8:  @ 0x08A497A8
	.incbin "graphics/misc/Img_StaffReelEnt_08A497A8.4bpp.lz"@ 0xA4A9D4 - 0xA497A8

	.global Img_StaffReelEnt_08A4A9D4
Img_StaffReelEnt_08A4A9D4:  @ 0x08A4A9D4
	.incbin "graphics/misc/Img_StaffReelEnt_08A4A9D4.4bpp.lz"@ 0xA4AE08 - 0xA4A9D4

	.global Tsa_StaffReelEnt_08A4AE08
Tsa_StaffReelEnt_08A4AE08:  @ 0x08A4AE08
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4AE08.map.bin.lz"@ 0xA4B090 - 0xA4AE08

	.global Tsa_StaffReelEnt_08A4B090
Tsa_StaffReelEnt_08A4B090:  @ 0x08A4B090
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4B090.map.bin.lz"@ 0xA4B2F4 - 0xA4B090

	.global Tsa_StaffReelEnt_08A4B2F4
Tsa_StaffReelEnt_08A4B2F4:  @ 0x08A4B2F4
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4B2F4.map.bin.lz"@ 0xA4B558 - 0xA4B2F4

	.global Tsa_StaffReelEnt_08A4B558
Tsa_StaffReelEnt_08A4B558:  @ 0x08A4B558
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4B558.map.bin.lz"@ 0xA4B788 - 0xA4B558

	.global Tsa_StaffReelEnt_08A4B788
Tsa_StaffReelEnt_08A4B788:  @ 0x08A4B788
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4B788.map.bin.lz"@ 0xA4BB50 - 0xA4B788

	.global Tsa_StaffReelEnt_08A4BB50
Tsa_StaffReelEnt_08A4BB50:  @ 0x08A4BB50
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4BB50.map.bin.lz"@ 0xA4BCC4 - 0xA4BB50

	.global Tsa_StaffReelEnt_08A4BCC4
Tsa_StaffReelEnt_08A4BCC4:  @ 0x08A4BCC4
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4BCC4.map.bin.lz"@ 0xA4BEC0 - 0xA4BCC4

	.global Tsa_StaffReelEnt_08A4BEC0
Tsa_StaffReelEnt_08A4BEC0:  @ 0x08A4BEC0
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4BEC0.map.bin.lz"@ 0xA4C0E4 - 0xA4BEC0

	.global Tsa_StaffReelEnt_08A4C0E4
Tsa_StaffReelEnt_08A4C0E4:  @ 0x08A4C0E4
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4C0E4.map.bin.lz"@ 0xA4C308 - 0xA4C0E4

	.global Tsa_StaffReelEnt_08A4C308
Tsa_StaffReelEnt_08A4C308:  @ 0x08A4C308
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4C308.map.bin.lz"@ 0xA4C6EC - 0xA4C308

	.global Tsa_StaffReelEnt_08A4C6EC
Tsa_StaffReelEnt_08A4C6EC:  @ 0x08A4C6EC
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4C6EC.map.bin.lz"@ 0xA4C9F0 - 0xA4C6EC

	.global Tsa_StaffReelEnt_08A4C9F0
Tsa_StaffReelEnt_08A4C9F0:  @ 0x08A4C9F0
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4C9F0.map.bin.lz"@ 0xA4CD40 - 0xA4C9F0

	.global Tsa_StaffReelEnt_08A4CD40
Tsa_StaffReelEnt_08A4CD40:  @ 0x08A4CD40
	.incbin "graphics/misc/Tsa_StaffReelEnt_08A4CD40.map.bin.lz"@ 0xA4CF2C - 0xA4CD40

	.global gGfx_BrownTextBox
gGfx_BrownTextBox:  @ 0x08A4CF2C
	.incbin "graphics/misc/gGfx_BrownTextBox.4bpp.lz"

	.global gPal_BrownTextBox
gPal_BrownTextBox:  @ 0x08A4D0CC
	.incbin "graphics/misc/gPal_BrownTextBox.agbpal"

	.align 2, 0
	.global cg_0_part_0_tiles
cg_0_part_0_tiles: @8a4d1e8
	.incbin "graphics/cg/cg_0_part_0.4bpp.lz"

	.align 2, 0
	.global cg_0_part_1_tiles
cg_0_part_1_tiles: @8a4d7b8
	.incbin "graphics/cg/cg_0_part_1.4bpp.lz"

	.align 2, 0
	.global cg_0_part_2_tiles
cg_0_part_2_tiles: @8a4dfb8
	.incbin "graphics/cg/cg_0_part_2.4bpp.lz"

	.align 2, 0
	.global cg_0_part_3_tiles
cg_0_part_3_tiles: @8a4e7dc
	.incbin "graphics/cg/cg_0_part_3.4bpp.lz"

	.align 2, 0
	.global cg_0_part_4_tiles
cg_0_part_4_tiles: @8a4f040
	.incbin "graphics/cg/cg_0_part_4.4bpp.lz"

	.align 2, 0
	.global cg_0_part_5_tiles
cg_0_part_5_tiles: @8a4f898
	.incbin "graphics/cg/cg_0_part_5.4bpp.lz"

	.align 2, 0
	.global cg_0_part_6_tiles
cg_0_part_6_tiles: @8a50118
	.incbin "graphics/cg/cg_0_part_6.4bpp.lz"

	.align 2, 0
	.global cg_0_part_7_tiles
cg_0_part_7_tiles: @8a50980
	.incbin "graphics/cg/cg_0_part_7.4bpp.lz"

	.align 2, 0
	.global cg_0_part_8_tiles
cg_0_part_8_tiles: @8a511f0
	.incbin "graphics/cg/cg_0_part_8.4bpp.lz"

	.align 2, 0
	.global cg_0_part_9_tiles
cg_0_part_9_tiles: @8a51a50
	.incbin "graphics/cg/cg_0_part_9.4bpp.lz"

	.align 2, 0
	.global cg_1_part_0_tiles
cg_1_part_0_tiles: @8a52258
	.incbin "graphics/cg/cg_1_part_0.4bpp.lz"

	.align 2, 0
	.global cg_1_part_1_tiles
cg_1_part_1_tiles: @8a529e4
	.incbin "graphics/cg/cg_1_part_1.4bpp.lz"

	.align 2, 0
	.global cg_1_part_2_tiles
cg_1_part_2_tiles: @8a531fc
	.incbin "graphics/cg/cg_1_part_2.4bpp.lz"

	.align 2, 0
	.global cg_1_part_3_tiles
cg_1_part_3_tiles: @8a53a10
	.incbin "graphics/cg/cg_1_part_3.4bpp.lz"

	.align 2, 0
	.global cg_1_part_4_tiles
cg_1_part_4_tiles: @8a5421c
	.incbin "graphics/cg/cg_1_part_4.4bpp.lz"

	.align 2, 0
	.global cg_1_part_5_tiles
cg_1_part_5_tiles: @8a54a30
	.incbin "graphics/cg/cg_1_part_5.4bpp.lz"

	.align 2, 0
	.global cg_1_part_6_tiles
cg_1_part_6_tiles: @8a55274
	.incbin "graphics/cg/cg_1_part_6.4bpp.lz"

	.align 2, 0
	.global cg_1_part_7_tiles
cg_1_part_7_tiles: @8a55a1c
	.incbin "graphics/cg/cg_1_part_7.4bpp.lz"

	.align 2, 0
	.global cg_1_part_8_tiles
cg_1_part_8_tiles: @8a56210
	.incbin "graphics/cg/cg_1_part_8.4bpp.lz"

	.align 2, 0
	.global cg_1_part_9_tiles
cg_1_part_9_tiles: @8a56a24
	.incbin "graphics/cg/cg_1_part_9.4bpp.lz"

	.align 2, 0
	.global cg_2_part_0_tiles
cg_2_part_0_tiles: @8a57200
	.incbin "graphics/cg/cg_2_part_0.4bpp.lz"

	.align 2, 0
	.global cg_2_part_1_tiles
cg_2_part_1_tiles: @8a57a30
	.incbin "graphics/cg/cg_2_part_1.4bpp.lz"

	.align 2, 0
	.global cg_2_part_2_tiles
cg_2_part_2_tiles: @8a58288
	.incbin "graphics/cg/cg_2_part_2.4bpp.lz"

	.align 2, 0
	.global cg_2_part_3_tiles
cg_2_part_3_tiles: @8a58ae4
	.incbin "graphics/cg/cg_2_part_3.4bpp.lz"

	.align 2, 0
	.global cg_2_part_4_tiles
cg_2_part_4_tiles: @8a59358
	.incbin "graphics/cg/cg_2_part_4.4bpp.lz"

	.align 2, 0
	.global cg_2_part_5_tiles
cg_2_part_5_tiles: @8a59bd8
	.incbin "graphics/cg/cg_2_part_5.4bpp.lz"

	.align 2, 0
	.global cg_2_part_6_tiles
cg_2_part_6_tiles: @8a5a434
	.incbin "graphics/cg/cg_2_part_6.4bpp.lz"

	.align 2, 0
	.global cg_2_part_7_tiles
cg_2_part_7_tiles: @8a5ac34
	.incbin "graphics/cg/cg_2_part_7.4bpp.lz"

	.align 2, 0
	.global cg_2_part_8_tiles
cg_2_part_8_tiles: @8a5b390
	.incbin "graphics/cg/cg_2_part_8.4bpp.lz"

	.align 2, 0
	.global cg_2_part_9_tiles
cg_2_part_9_tiles: @8a5bb54
	.incbin "graphics/cg/cg_2_part_9.4bpp.lz"

	.align 2, 0
	.global cg_3_part_0_tiles
cg_3_part_0_tiles: @8a5c350
	.incbin "graphics/cg/cg_3_part_0.4bpp.lz"

	.align 2, 0
	.global cg_3_part_1_tiles
cg_3_part_1_tiles: @8a5cb20
	.incbin "graphics/cg/cg_3_part_1.4bpp.lz"

	.align 2, 0
	.global cg_3_part_2_tiles
cg_3_part_2_tiles: @8a5d2f8
	.incbin "graphics/cg/cg_3_part_2.4bpp.lz"

	.align 2, 0
	.global cg_3_part_3_tiles
cg_3_part_3_tiles: @8a5da70
	.incbin "graphics/cg/cg_3_part_3.4bpp.lz"

	.align 2, 0
	.global cg_3_part_4_tiles
cg_3_part_4_tiles: @8a5e27c
	.incbin "graphics/cg/cg_3_part_4.4bpp.lz"

	.align 2, 0
	.global cg_3_part_5_tiles
cg_3_part_5_tiles: @8a5ea9c
	.incbin "graphics/cg/cg_3_part_5.4bpp.lz"

	.align 2, 0
	.global cg_3_part_6_tiles
cg_3_part_6_tiles: @8a5f2f0
	.incbin "graphics/cg/cg_3_part_6.4bpp.lz"

	.align 2, 0
	.global cg_3_part_7_tiles
cg_3_part_7_tiles: @8a5fb20
	.incbin "graphics/cg/cg_3_part_7.4bpp.lz"

	.align 2, 0
	.global cg_3_part_8_tiles
cg_3_part_8_tiles: @8a602ec
	.incbin "graphics/cg/cg_3_part_8.4bpp.lz"

	.align 2, 0
	.global cg_3_part_9_tiles
cg_3_part_9_tiles: @8a60b24
	.incbin "graphics/cg/cg_3_part_9.4bpp.lz"

	.align 2, 0
	.global cg_4_part_0_tiles
cg_4_part_0_tiles: @8a61388
	.incbin "graphics/cg/cg_4_part_0.4bpp.lz"

	.align 2, 0
	.global cg_4_part_1_tiles
cg_4_part_1_tiles: @8a61bc4
	.incbin "graphics/cg/cg_4_part_1.4bpp.lz"

	.align 2, 0
	.global cg_4_part_2_tiles
cg_4_part_2_tiles: @8a62428
	.incbin "graphics/cg/cg_4_part_2.4bpp.lz"

	.align 2, 0
	.global cg_4_part_3_tiles
cg_4_part_3_tiles: @8a62c8c
	.incbin "graphics/cg/cg_4_part_3.4bpp.lz"

	.align 2, 0
	.global cg_4_part_4_tiles
cg_4_part_4_tiles: @8a634ec
	.incbin "graphics/cg/cg_4_part_4.4bpp.lz"

	.align 2, 0
	.global cg_4_part_5_tiles
cg_4_part_5_tiles: @8a63d64
	.incbin "graphics/cg/cg_4_part_5.4bpp.lz"

	.align 2, 0
	.global cg_4_part_6_tiles
cg_4_part_6_tiles: @8a645d0
	.incbin "graphics/cg/cg_4_part_6.4bpp.lz"

	.align 2, 0
	.global cg_4_part_7_tiles
cg_4_part_7_tiles: @8a64dec
	.incbin "graphics/cg/cg_4_part_7.4bpp.lz"

	.align 2, 0
	.global cg_4_part_8_tiles
cg_4_part_8_tiles: @8a6561c
	.incbin "graphics/cg/cg_4_part_8.4bpp.lz"

	.align 2, 0
	.global cg_4_part_9_tiles
cg_4_part_9_tiles: @8a65e34
	.incbin "graphics/cg/cg_4_part_9.4bpp.lz"

	.align 2, 0
	.global cg_5_part_0_tiles
cg_5_part_0_tiles: @8a66634
	.incbin "graphics/cg/cg_5_part_0.4bpp.lz"

	.align 2, 0
	.global cg_5_part_1_tiles
cg_5_part_1_tiles: @8a66de4
	.incbin "graphics/cg/cg_5_part_1.4bpp.lz"

	.align 2, 0
	.global cg_5_part_2_tiles
cg_5_part_2_tiles: @8a675c8
	.incbin "graphics/cg/cg_5_part_2.4bpp.lz"

	.align 2, 0
	.global cg_5_part_3_tiles
cg_5_part_3_tiles: @8a67dc0
	.incbin "graphics/cg/cg_5_part_3.4bpp.lz"

	.align 2, 0
	.global cg_5_part_4_tiles
cg_5_part_4_tiles: @8a685fc
	.incbin "graphics/cg/cg_5_part_4.4bpp.lz"

	.align 2, 0
	.global cg_5_part_5_tiles
cg_5_part_5_tiles: @8a68e38
	.incbin "graphics/cg/cg_5_part_5.4bpp.lz"

	.align 2, 0
	.global cg_5_part_6_tiles
cg_5_part_6_tiles: @8a69694
	.incbin "graphics/cg/cg_5_part_6.4bpp.lz"

	.align 2, 0
	.global cg_5_part_7_tiles
cg_5_part_7_tiles: @8a69ec4
	.incbin "graphics/cg/cg_5_part_7.4bpp.lz"

	.align 2, 0
	.global cg_5_part_8_tiles
cg_5_part_8_tiles: @8a6a6cc
	.incbin "graphics/cg/cg_5_part_8.4bpp.lz"

	.align 2, 0
	.global cg_5_part_9_tiles
cg_5_part_9_tiles: @8a6ae84
	.incbin "graphics/cg/cg_5_part_9.4bpp.lz"

	.align 2, 0
	.global cg_6_part_0_tiles
cg_6_part_0_tiles: @8a6b5c4
	.incbin "graphics/cg/cg_6_part_0.4bpp.lz"

	.align 2, 0
	.global cg_6_part_1_tiles
cg_6_part_1_tiles: @8a6bdd8
	.incbin "graphics/cg/cg_6_part_1.4bpp.lz"

	.align 2, 0
	.global cg_6_part_2_tiles
cg_6_part_2_tiles: @8a6c62c
	.incbin "graphics/cg/cg_6_part_2.4bpp.lz"

	.align 2, 0
	.global cg_6_part_3_tiles
cg_6_part_3_tiles: @8a6ce80
	.incbin "graphics/cg/cg_6_part_3.4bpp.lz"

	.align 2, 0
	.global cg_6_part_4_tiles
cg_6_part_4_tiles: @8a6d6dc
	.incbin "graphics/cg/cg_6_part_4.4bpp.lz"

	.align 2, 0
	.global cg_6_part_5_tiles
cg_6_part_5_tiles: @8a6df50
	.incbin "graphics/cg/cg_6_part_5.4bpp.lz"

	.align 2, 0
	.global cg_6_part_6_tiles
cg_6_part_6_tiles: @8a6e7cc
	.incbin "graphics/cg/cg_6_part_6.4bpp.lz"

	.align 2, 0
	.global cg_6_part_7_tiles
cg_6_part_7_tiles: @8a6f040
	.incbin "graphics/cg/cg_6_part_7.4bpp.lz"

	.align 2, 0
	.global cg_6_part_8_tiles
cg_6_part_8_tiles: @8a6f894
	.incbin "graphics/cg/cg_6_part_8.4bpp.lz"

	.align 2, 0
	.global cg_6_part_9_tiles
cg_6_part_9_tiles: @8a700e4
	.incbin "graphics/cg/cg_6_part_9.4bpp.lz"

	.align 2, 0
	.global cg_7_part_0_tiles
cg_7_part_0_tiles: @8a708f4
	.incbin "graphics/cg/cg_7_part_0.4bpp.lz"

	.align 2, 0
	.global cg_7_part_1_tiles
cg_7_part_1_tiles: @8a70f04
	.incbin "graphics/cg/cg_7_part_1.4bpp.lz"

	.align 2, 0
	.global cg_7_part_2_tiles
cg_7_part_2_tiles: @8a716e0
	.incbin "graphics/cg/cg_7_part_2.4bpp.lz"

	.align 2, 0
	.global cg_7_part_3_tiles
cg_7_part_3_tiles: @8a71ee4
	.incbin "graphics/cg/cg_7_part_3.4bpp.lz"

	.align 2, 0
	.global cg_7_part_4_tiles
cg_7_part_4_tiles: @8a72718
	.incbin "graphics/cg/cg_7_part_4.4bpp.lz"

	.align 2, 0
	.global cg_7_part_5_tiles
cg_7_part_5_tiles: @8a72f44
	.incbin "graphics/cg/cg_7_part_5.4bpp.lz"

	.align 2, 0
	.global cg_7_part_6_tiles
cg_7_part_6_tiles: @8a737a0
	.incbin "graphics/cg/cg_7_part_6.4bpp.lz"

	.align 2, 0
	.global cg_7_part_7_tiles
cg_7_part_7_tiles: @8a73ff0
	.incbin "graphics/cg/cg_7_part_7.4bpp.lz"

	.align 2, 0
	.global cg_7_part_8_tiles
cg_7_part_8_tiles: @8a7480c
	.incbin "graphics/cg/cg_7_part_8.4bpp.lz"

	.align 2, 0
	.global cg_7_part_9_tiles
cg_7_part_9_tiles: @8a74ff4
	.incbin "graphics/cg/cg_7_part_9.4bpp.lz"

	.align 2, 0
	.global cg_8_part_0_tiles
cg_8_part_0_tiles: @8a75838
	.incbin "graphics/cg/cg_8_part_0.4bpp.lz"

	.align 2, 0
	.global cg_8_part_1_tiles
cg_8_part_1_tiles: @8a75fb0
	.incbin "graphics/cg/cg_8_part_1.4bpp.lz"

	.align 2, 0
	.global cg_8_part_2_tiles
cg_8_part_2_tiles: @8a767fc
	.incbin "graphics/cg/cg_8_part_2.4bpp.lz"

	.align 2, 0
	.global cg_8_part_3_tiles
cg_8_part_3_tiles: @8a77070
	.incbin "graphics/cg/cg_8_part_3.4bpp.lz"

	.align 2, 0
	.global cg_8_part_4_tiles
cg_8_part_4_tiles: @8a778d8
	.incbin "graphics/cg/cg_8_part_4.4bpp.lz"

	.align 2, 0
	.global cg_8_part_5_tiles
cg_8_part_5_tiles: @8a780ec
	.incbin "graphics/cg/cg_8_part_5.4bpp.lz"

	.align 2, 0
	.global cg_8_part_6_tiles
cg_8_part_6_tiles: @8a7892c
	.incbin "graphics/cg/cg_8_part_6.4bpp.lz"

	.align 2, 0
	.global cg_8_part_7_tiles
cg_8_part_7_tiles: @8a79188
	.incbin "graphics/cg/cg_8_part_7.4bpp.lz"

	.align 2, 0
	.global cg_8_part_8_tiles
cg_8_part_8_tiles: @8a799ec
	.incbin "graphics/cg/cg_8_part_8.4bpp.lz"

	.align 2, 0
	.global cg_8_part_9_tiles
cg_8_part_9_tiles: @8a7a218
	.incbin "graphics/cg/cg_8_part_9.4bpp.lz"

	.align 2, 0
	.global cg_9_part_0_tiles
cg_9_part_0_tiles: @8a7aa0c
	.incbin "graphics/cg/cg_9_part_0.4bpp.lz"

	.align 2, 0
	.global cg_9_part_1_tiles
cg_9_part_1_tiles: @8a7b1f4
	.incbin "graphics/cg/cg_9_part_1.4bpp.lz"

	.align 2, 0
	.global cg_9_part_2_tiles
cg_9_part_2_tiles: @8a7ba2c
	.incbin "graphics/cg/cg_9_part_2.4bpp.lz"

	.align 2, 0
	.global cg_9_part_3_tiles
cg_9_part_3_tiles: @8a7c280
	.incbin "graphics/cg/cg_9_part_3.4bpp.lz"

	.align 2, 0
	.global cg_9_part_4_tiles
cg_9_part_4_tiles: @8a7cad0
	.incbin "graphics/cg/cg_9_part_4.4bpp.lz"

	.align 2, 0
	.global cg_9_part_5_tiles
cg_9_part_5_tiles: @8a7d324
	.incbin "graphics/cg/cg_9_part_5.4bpp.lz"

	.align 2, 0
	.global cg_9_part_6_tiles
cg_9_part_6_tiles: @8a7db70
	.incbin "graphics/cg/cg_9_part_6.4bpp.lz"

	.align 2, 0
	.global cg_9_part_7_tiles
cg_9_part_7_tiles: @8a7e3e0
	.incbin "graphics/cg/cg_9_part_7.4bpp.lz"

	.align 2, 0
	.global cg_9_part_8_tiles
cg_9_part_8_tiles: @8a7ec3c
	.incbin "graphics/cg/cg_9_part_8.4bpp.lz"

	.align 2, 0
	.global cg_9_part_9_tiles
cg_9_part_9_tiles: @8a7f494
	.incbin "graphics/cg/cg_9_part_9.4bpp.lz"

	.align 2, 0
	.global cg_0_map
cg_0_map: @8a7fcdc
	.incbin "graphics/cg/cg_0.map.bin"

	.align 2, 0
	.global cg_1_map
cg_1_map: @8a80190
	.incbin "graphics/cg/cg_1.map.bin"

	.align 2, 0
	.global cg_2_map
cg_2_map: @8a80644
	.incbin "graphics/cg/cg_2.map.bin"

	.align 2, 0
	.global cg_3_map
cg_3_map: @8a80af8
	.incbin "graphics/cg/cg_3.map.bin"

	.align 2, 0
	.global cg_4_map
cg_4_map: @8a80fac
	.incbin "graphics/cg/cg_4.map.bin"

	.align 2, 0
	.global cg_5_map
cg_5_map: @8a81460
	.incbin "graphics/cg/cg_5.map.bin"

	.align 2, 0
	.global cg_6_map
cg_6_map: @8a81914
	.incbin "graphics/cg/cg_6.map.bin"

	.align 2, 0
	.global cg_7_map
cg_7_map: @8a81dc8
	.incbin "graphics/cg/cg_7.map.bin"

	.align 2, 0
	.global cg_8_map
cg_8_map: @8a8227c
	.incbin "graphics/cg/cg_8.map.bin"

	.align 2, 0
	.global cg_9_map
cg_9_map: @8a82730
	.incbin "graphics/cg/cg_9.map.bin"

	.align 2, 0
	.global cg_0_palette
cg_0_palette: @8a82be4
	.incbin "graphics/cg/cg_0.gbapal"

	.align 2, 0
	.global cg_1_palette
cg_1_palette: @8a82ca4
	.incbin "graphics/cg/cg_1.gbapal"

	.align 2, 0
	.global cg_2_palette
cg_2_palette: @8a82d64
	.incbin "graphics/cg/cg_2.gbapal"

	.align 2, 0
	.global cg_3_palette
cg_3_palette: @8a82e24
	.incbin "graphics/cg/cg_3.gbapal"

	.align 2, 0
	.global cg_4_palette
cg_4_palette: @8a82ee4
	.incbin "graphics/cg/cg_4.gbapal"

	.align 2, 0
	.global cg_5_palette
cg_5_palette: @8a82fa4
	.incbin "graphics/cg/cg_5.gbapal"

	.align 2, 0
	.global cg_6_palette
cg_6_palette: @8a83064
	.incbin "graphics/cg/cg_6.gbapal"

	.align 2, 0
	.global cg_7_palette
cg_7_palette: @8a83124
	.incbin "graphics/cg/cg_7.gbapal"

	.align 2, 0
	.global cg_8_palette
cg_8_palette: @8a831e4
	.incbin "graphics/cg/cg_8.gbapal"

	.align 2, 0
	.global cg_9_palette
cg_9_palette: @8a832a4
	.incbin "graphics/cg/cg_9.gbapal"

	.global gUnknown_08A83364
gUnknown_08A83364:  @ 0x08A83364
	.incbin "graphics/misc/gUnknown_08A83364.4bpp"

	.global gUnknown_08A95F64
gUnknown_08A95F64:  @ 0x08A95F64
	.incbin "graphics/misc/gUnknown_08A95F64.4bpp"

	.global gUnknown_08A95FE4
gUnknown_08A95FE4:  @ 0x08A95FE4
	.incbin "graphics/misc/gUnknown_08A95FE4.4bpp"

	.global gUnknown_08A96064
gUnknown_08A96064:  @ 0x08A96064
	.incbin "graphics/misc/gUnknown_08A96064.4bpp.lz"

	.global Img_GmapNodes
Img_GmapNodes:  @ 0x08A96308
	.incbin "graphics/misc/Img_GmapNodes.4bpp.lz"

	.global Img_GmapCastleNodes
Img_GmapCastleNodes:  @ 0x08A97410
	.incbin "graphics/misc/Img_GmapCastleNodes.4bpp.lz"

	.global gUnknown_08A97A40
gUnknown_08A97A40:  @ 0x08A97A40
	.incbin "graphics/misc/gUnknown_08A97A40.4bpp"

	.global gPal_GMapPI_ShopIcons
gPal_GMapPI_ShopIcons:  @ 0x08A97A60
	.incbin "graphics/misc/gPal_GMapPI_ShopIcons.gbapal"

	.global gGfx_GMapPI_ShopIcons
gGfx_GMapPI_ShopIcons:  @ 0x08A97A80
	.incbin "graphics/misc/gGfx_GMapPI_ShopIcons.4bpp.lz"

	.global gPal_08A97ACC
gPal_08A97ACC:  @ 0x08A97ACC
	.incbin "graphics/misc/gPal_08A97ACC.gbapal"

	.global Sprite_08A97AEC
Sprite_08A97AEC:  @ 0x08A97AEC
	@ AP animation definition (frame_list +0x4, anim_list +0x26); flat halfword table, 428 bytes
	.2byte 0x0004, 0x0026, 0x0026, 0x0034, 0x0042, 0x0056, 0x0070, 0x0090
	.2byte 0x00B6, 0x00DC, 0x00F0, 0x0104, 0x0112, 0x011A, 0x0122, 0x0136
	.2byte 0x013E, 0x0146, 0x014E, 0x0134, 0x0170, 0x0002, 0x40F8, 0x41F0
	.2byte 0x10A0, 0x00F4, 0x01F8, 0x1081, 0x0002, 0x00F0, 0x01F8, 0x1082
	.2byte 0x40F8, 0x41F0, 0x10A4, 0x0003, 0x00EC, 0x01F8, 0x1082, 0x00F2
	.2byte 0x0000, 0x1081, 0x40F0, 0x81F0, 0x1088, 0x0004, 0x00EA, 0x01F8
	.2byte 0x1081, 0x00EE, 0x0000, 0x1082, 0x00F2, 0x01F4, 0x1081, 0x40F0
	.2byte 0x81F0, 0x108C, 0x0005, 0x00E9, 0x01F8, 0x1080, 0x00EA, 0x0000
	.2byte 0x1082, 0x00EE, 0x01F4, 0x1082, 0x00F2, 0x0004, 0x1081, 0x40F0
	.2byte 0x81F0, 0x1090, 0x0006, 0x00E8, 0x0000, 0x1081, 0x00EC, 0x01F4
	.2byte 0x1081, 0x00EE, 0x0004, 0x1082, 0x00F0, 0x01FC, 0x1081, 0x40F0
	.2byte 0x81F0, 0x1094, 0x40F8, 0x41F0, 0x10A0, 0x0006, 0x00E7, 0x0000
	.2byte 0x1080, 0x00EB, 0x01F4, 0x1080, 0x00ED, 0x0004, 0x1082, 0x00EE
	.2byte 0x01FC, 0x1082, 0x40F0, 0x81F0, 0x1098, 0x40F8, 0x41F0, 0x10A4
	.2byte 0x0003, 0x40F0, 0x81F0, 0x108C, 0x00EC, 0x0004, 0x1081, 0x00EC
	.2byte 0x01FC, 0x1082, 0x0003, 0x40F0, 0x81F0, 0x1090, 0x00EB, 0x0004
	.2byte 0x1080, 0x00EA, 0x01FC, 0x1081, 0x0002, 0x40F0, 0x81F0, 0x1094
	.2byte 0x00E9, 0x01FC, 0x1080, 0x0001, 0x40F0, 0x81F0, 0x1098, 0x0001
	.2byte 0x40F0, 0x81F0, 0x109C, 0x0003, 0x00EE, 0x01FF, 0x1080, 0x00F3
	.2byte 0x0006, 0x1080, 0x00F0, 0x01F8, 0x1080, 0x0001, 0x40F5, 0x81F8
	.2byte 0x10C0, 0x0001, 0x40F5, 0x81F8, 0x10C4, 0x0001, 0x40F5, 0x81F8
	.2byte 0x10C8, 0x0001, 0x40F5, 0x81F8, 0x10CC, 0x0003, 0x0000, 0x0004
	.2byte 0x0001, 0x0005, 0x0002, 0x0005, 0x0003, 0x0005, 0x0004, 0x0005
	.2byte 0x0005, 0x0005, 0x0006, 0x0005, 0x0007, 0x0005, 0x0008, 0x0006
	.2byte 0x0009, 0x0007, 0x000A, 0x0008, 0x000B, 0x0007, 0x000C, 0x0000
	.2byte 0x0001, 0x0000, 0xFFFF, 0x000D, 0x000D, 0x000C, 0x000E, 0x000D
	.2byte 0x000F, 0x000D, 0x0010, 0x0000, 0xFFFF, 0x0000
.L_end_Sprite_08A97AEC:
	.if (.L_end_Sprite_08A97AEC - Sprite_08A97AEC) != 428
	.error "Sprite_08A97AEC size mismatch"
	.endif

	.global gImg_WorldmapNodeRevealEffect
gImg_WorldmapNodeRevealEffect:  @ 0x08A97C98
	.incbin "graphics/misc/gImg_WorldmapNodeRevealEffect.4bpp.lz"

	.global gPal_WorldmapNodeRevealEffect
gPal_WorldmapNodeRevealEffect:  @ 0x08A97E28
	.incbin "graphics/misc/gPal_WorldmapNodeRevealEffect.gbapal"

	.global gUnknown_08A97E48
gUnknown_08A97E48:  @ 0x08A97E48
	@ RGB15 color/palette table (72 u16 entries, 144 bytes)
	.2byte 0x7BC7, 0x7EE3, 0x7622, 0x7EE3, 0x7BC7, 0x6D43, 0x48E7, 0x48E7
	.2byte 0x48E7, 0x48E7, 0x48E7, 0x48E7, 0x48E7, 0x6543, 0x7622, 0x7EE3
	.2byte 0x6543, 0x6543, 0x6543, 0x6543, 0x6543, 0x6543, 0x6543, 0x6543
	.2byte 0x6543, 0x6543, 0x6543, 0x6543, 0x6543, 0x6543, 0x7622, 0x7EE3
	.2byte 0x0010, 0x0001, 0x1103, 0x1111, 0x0011, 0xF000, 0x5001, 0xFF01
	.2byte 0x1F10, 0x23F0, 0x1FF0, 0x23F0, 0x1FF0, 0x23F0, 0x1FF0, 0x23D0
	.2byte 0xF0FC, 0xB01D, 0xF023, 0xB019, 0xF023, 0xB015, 0x0001, 0x0000
	.2byte 0x0000, 0x0000, 0x0842, 0x1084, 0x18C6, 0x2529, 0x2D6B, 0x35AD
	.2byte 0x3DEF, 0x4A52, 0x5294, 0x5AD6, 0x6739, 0x6F7B, 0x77BD, 0x7FFF
.L_end_gUnknown_08A97E48:
	.if (.L_end_gUnknown_08A97E48 - gUnknown_08A97E48) != 144
	.error "gUnknown_08A97E48 size mismatch"
	.endif

	.global Img_GmapPath
Img_GmapPath:  @ 0x08A97ED8
	.incbin "graphics/misc/Img_GmapPath.4bpp.lz"

	.global gUnknown_08A97FA4
gUnknown_08A97FA4:  @ 0x08A97FA4
	.incbin "graphics/misc/gUnknown_08A97FA4.4bpp"

	.global gUnknown_08A97FC4
gUnknown_08A97FC4:  @ 0x08A97FC4
	.byte 12, 8, 3, 1  @ rect 0: x=12 y=8 w=3 h=1
	.2byte 0x0001, 0x0001, 0x0007
	.byte 12, 9, 4, 1  @ rect 1: x=12 y=9 w=4 h=1
	.2byte 0x0002, 0x0002, 0x0006, 0x0007
	.byte 14, 10, 5, 1  @ rect 2: x=14 y=10 w=5 h=1
	.2byte 0x0C07, 0x0006, 0x0001, 0x0001, 0x0001
	.byte 15, 11, 4, 1  @ rect 3: x=15 y=11 w=4 h=1
	.2byte 0x0C07, 0x0002, 0x0002, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A97FC4:
	.if (.L_end_gUnknown_08A97FC4 - gUnknown_08A97FC4) != 52
	.error "gUnknown_08A97FC4 size mismatch"
	.endif

	.global gUnknown_08A97FF8
gUnknown_08A97FF8:  @ 0x08A97FF8
	.byte 11, 9, 2, 1  @ rect 0: x=11 y=9 w=2 h=1
	.2byte 0x0006, 0x0007
	.byte 11, 10, 3, 1  @ rect 1: x=11 y=10 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 12, 11, 3, 1  @ rect 2: x=12 y=11 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 13, 12, 3, 1  @ rect 3: x=13 y=12 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 14, 13, 2, 1  @ rect 4: x=14 y=13 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 14, 14, 2, 1  @ rect 5: x=14 y=14 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A97FF8:
	.if (.L_end_gUnknown_08A97FF8 - gUnknown_08A97FF8) != 56
	.error "gUnknown_08A97FF8 size mismatch"
	.endif

	.global gUnknown_08A98030
gUnknown_08A98030:  @ 0x08A98030
	.byte 14, 15, 2, 1  @ rect 0: x=14 y=15 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 14, 16, 2, 1  @ rect 1: x=14 y=16 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 14, 17, 2, 1  @ rect 2: x=14 y=17 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 14, 18, 2, 1  @ rect 3: x=14 y=18 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98030:
	.if (.L_end_gUnknown_08A98030 - gUnknown_08A98030) != 36
	.error "gUnknown_08A98030 size mismatch"
	.endif

	.global gUnknown_08A98054
gUnknown_08A98054:  @ 0x08A98054
	.byte 16, 18, 3, 1  @ rect 0: x=16 y=18 w=3 h=1
	.2byte 0x0001, 0x0001, 0x0001
	.byte 16, 19, 3, 1  @ rect 1: x=16 y=19 w=3 h=1
	.2byte 0x0002, 0x0002, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98054:
	.if (.L_end_gUnknown_08A98054 - gUnknown_08A98054) != 24
	.error "gUnknown_08A98054 size mismatch"
	.endif

	.global gUnknown_08A9806C
gUnknown_08A9806C:  @ 0x8A9806C
	.byte 18, 19, 2, 1  @ rect 0: x=18 y=19 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 20, 2, 1  @ rect 1: x=18 y=20 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 21, 2, 1  @ rect 2: x=18 y=21 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 22, 2, 1  @ rect 3: x=18 y=22 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A9806C:
	.if (.L_end_gUnknown_08A9806C - gUnknown_08A9806C) != 36
	.error "gUnknown_08A9806C size mismatch"
	.endif

	.global gUnknown_08A98090
gUnknown_08A98090:  @ 0x8A98090
	.byte 20, 22, 4, 1  @ rect 0: x=20 y=22 w=4 h=1
	.2byte 0x0001, 0x0001, 0x0001, 0x0007
	.byte 20, 23, 4, 1  @ rect 1: x=20 y=23 w=4 h=1
	.2byte 0x0002, 0x0002, 0x0005, 0x0004
	.byte 22, 24, 2, 1  @ rect 2: x=22 y=24 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98090:
	.if (.L_end_gUnknown_08A98090 - gUnknown_08A98090) != 36
	.error "gUnknown_08A98090 size mismatch"
	.endif

	.global gUnknown_08A980B4
gUnknown_08A980B4:  @ 0x8A980B4
	.byte 22, 25, 2, 1  @ rect 0: x=22 y=25 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 22, 26, 5, 1  @ rect 1: x=22 y=26 w=5 h=1
	.2byte 0x0003, 0x0C05, 0x0001, 0x0001, 0x0001
	.byte 22, 27, 5, 1  @ rect 2: x=22 y=27 w=5 h=1
	.2byte 0x0C07, 0x0002, 0x0002, 0x0002, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A980B4:
	.if (.L_end_gUnknown_08A980B4 - gUnknown_08A980B4) != 40
	.error "gUnknown_08A980B4 size mismatch"
	.endif

	.global gUnknown_08A980DC
gUnknown_08A980DC:  @ 0x8A980DC
	.byte 22, 25, 2, 1  @ rect 0: x=22 y=25 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 22, 26, 5, 1  @ rect 1: x=22 y=26 w=5 h=1
	.2byte 0x0003, 0x0C05, 0x0001, 0x0001, 0x0001
	.byte 22, 27, 5, 1  @ rect 2: x=22 y=27 w=5 h=1
	.2byte 0x0C07, 0x0002, 0x0002, 0x0002, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A980DC:
	.if (.L_end_gUnknown_08A980DC - gUnknown_08A980DC) != 40
	.error "gUnknown_08A980DC size mismatch"
	.endif

	.global gUnknown_08A98104
gUnknown_08A98104:  @ 0x8A98104
	.incbin "graphics/misc/gUnknown_08A98104.4bpp"

	.global gUnknown_08A98144
gUnknown_08A98144:  @ 0x8A98144
	.byte 18, 7, 2, 1  @ rect 0: x=18 y=7 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 8, 2, 1  @ rect 1: x=18 y=8 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 9, 2, 1  @ rect 2: x=18 y=9 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 10, 2, 1  @ rect 3: x=18 y=10 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98144:
	.if (.L_end_gUnknown_08A98144 - gUnknown_08A98144) != 36
	.error "gUnknown_08A98144 size mismatch"
	.endif

	.global gUnknown_08A98168
gUnknown_08A98168:  @ 0x8A98168
	.byte 26, 7, 2, 1  @ rect 0: x=26 y=7 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 26, 8, 2, 1  @ rect 1: x=26 y=8 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 26, 9, 2, 1  @ rect 2: x=26 y=9 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 26, 10, 2, 1  @ rect 3: x=26 y=10 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 26, 11, 2, 1  @ rect 4: x=26 y=11 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 26, 12, 2, 1  @ rect 5: x=26 y=12 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98168:
	.if (.L_end_gUnknown_08A98168 - gUnknown_08A98168) != 52
	.error "gUnknown_08A98168 size mismatch"
	.endif

	.global gUnknown_08A9819C
gUnknown_08A9819C:  @ 0x8A9819C
	.incbin "graphics/misc/gUnknown_08A9819C.4bpp"

	.global gUnknown_08A981BC
gUnknown_08A981BC:  @ 0x8A981BC
	.byte 33, 12, 4, 1  @ rect 0: x=33 y=12 w=4 h=1
	.2byte 0x0001, 0x0001, 0x0001, 0x0007
	.byte 33, 13, 5, 1  @ rect 1: x=33 y=13 w=5 h=1
	.2byte 0x0002, 0x0002, 0x0002, 0x0006, 0x0007
	.byte 36, 14, 3, 1  @ rect 2: x=36 y=14 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 37, 15, 3, 1  @ rect 3: x=37 y=15 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 38, 16, 2, 1  @ rect 4: x=38 y=16 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A981BC:
	.if (.L_end_gUnknown_08A981BC - gUnknown_08A981BC) != 56
	.error "gUnknown_08A981BC size mismatch"
	.endif

	.global gUnknown_08A981F4
gUnknown_08A981F4:  @ 0x8A981F4
	.byte 38, 17, 2, 1  @ rect 0: x=38 y=17 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 18, 2, 1  @ rect 1: x=38 y=18 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 19, 2, 1  @ rect 2: x=38 y=19 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 20, 2, 1  @ rect 3: x=38 y=20 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 21, 2, 1  @ rect 4: x=38 y=21 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 22, 2, 1  @ rect 5: x=38 y=22 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A981F4:
	.if (.L_end_gUnknown_08A981F4 - gUnknown_08A981F4) != 52
	.error "gUnknown_08A981F4 size mismatch"
	.endif

	.global gUnknown_08A98228
gUnknown_08A98228:  @ 0x8A98228
	.byte 9, 9, 3, 1  @ rect 0: x=9 y=9 w=3 h=1
	.2byte 0x0407, 0x0406, 0x0807
	.byte 9, 10, 2, 1  @ rect 1: x=9 y=10 w=2 h=1
	.2byte 0x0406, 0x0807
	.byte 8, 11, 2, 1  @ rect 2: x=8 y=11 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 12, 2, 1  @ rect 3: x=8 y=12 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 13, 2, 1  @ rect 4: x=8 y=13 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 14, 2, 1  @ rect 5: x=8 y=14 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 15, 2, 1  @ rect 6: x=8 y=15 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 16, 2, 1  @ rect 7: x=8 y=16 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 17, 2, 1  @ rect 8: x=8 y=17 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 18, 2, 1  @ rect 9: x=8 y=18 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A98228:
	.if (.L_end_gUnknown_08A98228 - gUnknown_08A98228) != 84
	.error "gUnknown_08A98228 size mismatch"
	.endif

	.global gUnknown_08A9827C
gUnknown_08A9827C:  @ 0x8A9827C
	.byte 8, 20, 2, 1  @ rect 0: x=8 y=20 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 21, 2, 1  @ rect 1: x=8 y=21 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 22, 2, 1  @ rect 2: x=8 y=22 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 8, 23, 2, 1  @ rect 3: x=8 y=23 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 7, 24, 3, 1  @ rect 4: x=7 y=24 w=3 h=1
	.2byte 0x0407, 0x0406, 0x0807
	.byte 6, 25, 3, 1  @ rect 5: x=6 y=25 w=3 h=1
	.2byte 0x0407, 0x0406, 0x0807
	.byte 6, 26, 2, 1  @ rect 6: x=6 y=26 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 6, 27, 2, 1  @ rect 7: x=6 y=27 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 6, 28, 3, 1  @ rect 8: x=6 y=28 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0001
	.byte 7, 29, 2, 1  @ rect 9: x=7 y=29 w=2 h=1
	.2byte 0x0C07, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A9827C:
	.if (.L_end_gUnknown_08A9827C - gUnknown_08A9827C) != 88
	.error "gUnknown_08A9827C size mismatch"
	.endif

	.global gUnknown_08A982D4
gUnknown_08A982D4:  @ 0x8A982D4
	.incbin "graphics/misc/gUnknown_08A982D4.4bpp"

	.global gUnknown_08A98314
gUnknown_08A98314:  @ 0x8A98314
	.byte 19, 30, 2, 1  @ rect 0: x=19 y=30 w=2 h=1
	.2byte 0x0001, 0x0007
	.byte 19, 31, 3, 1  @ rect 1: x=19 y=31 w=3 h=1
	.2byte 0x0002, 0x0006, 0x0007
	.byte 20, 32, 5, 1  @ rect 2: x=20 y=32 w=5 h=1
	.2byte 0x0C07, 0x0006, 0x0001, 0x0001, 0x0001
	.byte 21, 33, 4, 1  @ rect 3: x=21 y=33 w=4 h=1
	.2byte 0x0C07, 0x0002, 0x0002, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98314:
	.if (.L_end_gUnknown_08A98314 - gUnknown_08A98314) != 48
	.error "gUnknown_08A98314 size mismatch"
	.endif

	.global gUnknown_08A98344
gUnknown_08A98344:  @ 0x8A98344
	.byte 25, 32, 7, 1  @ rect 0: x=25 y=32 w=7 h=1
	.2byte 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001
	.byte 25, 33, 7, 1  @ rect 1: x=25 y=33 w=7 h=1
	.2byte 0x0002, 0x0002, 0x0002, 0x0002, 0x0002, 0x0002, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98344:
	.if (.L_end_gUnknown_08A98344 - gUnknown_08A98344) != 40
	.error "gUnknown_08A98344 size mismatch"
	.endif

	.global gUnknown_08A9836C
gUnknown_08A9836C:  @ 0x8A9836C
	.byte 38, 25, 2, 1  @ rect 0: x=38 y=25 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 26, 2, 1  @ rect 1: x=38 y=26 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 27, 2, 1  @ rect 2: x=38 y=27 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 28, 2, 1  @ rect 3: x=38 y=28 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 29, 2, 1  @ rect 4: x=38 y=29 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 37, 30, 3, 1  @ rect 5: x=37 y=30 w=3 h=1
	.2byte 0x0407, 0x0406, 0x0807
	.byte 36, 31, 3, 1  @ rect 6: x=36 y=31 w=3 h=1
	.2byte 0x0407, 0x0406, 0x0807
	.byte 35, 32, 3, 1  @ rect 7: x=35 y=32 w=3 h=1
	.2byte 0x0001, 0x0406, 0x0807
	.byte 35, 33, 2, 1  @ rect 8: x=35 y=33 w=2 h=1
	.2byte 0x0002, 0x0807
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A9836C:
	.if (.L_end_gUnknown_08A9836C - gUnknown_08A9836C) != 80
	.error "gUnknown_08A9836C size mismatch"
	.endif

	.global gUnknown_08A983BC
gUnknown_08A983BC:  @ 0x8A983BC
	.byte 28, 26, 3, 1  @ rect 0: x=28 y=26 w=3 h=1
	.2byte 0x0001, 0x0001, 0x0007
	.byte 28, 27, 4, 1  @ rect 1: x=28 y=27 w=4 h=1
	.2byte 0x0002, 0x0002, 0x0006, 0x0007
	.byte 30, 28, 3, 1  @ rect 2: x=30 y=28 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 31, 29, 3, 1  @ rect 3: x=31 y=29 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 32, 30, 2, 1  @ rect 4: x=32 y=30 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A983BC:
	.if (.L_end_gUnknown_08A983BC - gUnknown_08A983BC) != 52
	.error "gUnknown_08A983BC size mismatch"
	.endif

	.global gUnknown_08A983F0
gUnknown_08A983F0:  @ 0x8A983F0
	.byte 26, 19, 2, 1  @ rect 0: x=26 y=19 w=2 h=1
	.2byte 0x0006, 0x0007
	.byte 26, 20, 3, 1  @ rect 1: x=26 y=20 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 27, 21, 3, 1  @ rect 2: x=27 y=21 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 28, 22, 3, 1  @ rect 3: x=28 y=22 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 29, 23, 3, 1  @ rect 4: x=29 y=23 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 30, 24, 8, 1  @ rect 5: x=30 y=24 w=8 h=1
	.2byte 0x0C07, 0x0006, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001
	.byte 31, 25, 7, 1  @ rect 6: x=31 y=25 w=7 h=1
	.2byte 0x0C07, 0x0002, 0x0002, 0x0002, 0x0002, 0x0002, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A983F0:
	.if (.L_end_gUnknown_08A983F0 - gUnknown_08A983F0) != 88
	.error "gUnknown_08A983F0 size mismatch"
	.endif

	.global gUnknown_08A98448
gUnknown_08A98448:  @ 0x8A98448
	.byte 38, 17, 2, 1  @ rect 0: x=38 y=17 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 18, 2, 1  @ rect 1: x=38 y=18 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 19, 2, 1  @ rect 2: x=38 y=19 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 20, 2, 1  @ rect 3: x=38 y=20 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 21, 2, 1  @ rect 4: x=38 y=21 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 38, 22, 2, 1  @ rect 5: x=38 y=22 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98448:
	.if (.L_end_gUnknown_08A98448 - gUnknown_08A98448) != 52
	.error "gUnknown_08A98448 size mismatch"
	.endif

	.global gUnknown_08A9847C
gUnknown_08A9847C:  @ 0x8A9847C
	.byte 18, 11, 2, 1  @ rect 0: x=18 y=11 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 12, 2, 1  @ rect 1: x=18 y=12 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 13, 2, 1  @ rect 2: x=18 y=13 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 18, 14, 3, 1  @ rect 3: x=18 y=14 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 19, 15, 3, 1  @ rect 4: x=19 y=15 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 20, 16, 3, 1  @ rect 5: x=20 y=16 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 21, 17, 3, 1  @ rect 6: x=21 y=17 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 22, 18, 4, 1  @ rect 7: x=22 y=18 w=4 h=1
	.2byte 0x0C07, 0x0006, 0x0001, 0x0001
	.byte 23, 19, 3, 1  @ rect 8: x=23 y=19 w=3 h=1
	.2byte 0x0C07, 0x0002, 0x0002
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A9847C:
	.if (.L_end_gUnknown_08A9847C - gUnknown_08A9847C) != 88
	.error "gUnknown_08A9847C size mismatch"
	.endif

	.global gUnknown_08A984D4
gUnknown_08A984D4:  @ 0x8A984D4
	.byte 44, 21, 2, 1  @ rect 0: x=44 y=21 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 44, 22, 2, 1  @ rect 1: x=44 y=22 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 44, 23, 2, 1  @ rect 2: x=44 y=23 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 41, 24, 5, 1  @ rect 3: x=41 y=24 w=5 h=1
	.2byte 0x0001, 0x0001, 0x0001, 0x0805, 0x0004
	.byte 41, 25, 5, 1  @ rect 4: x=41 y=25 w=5 h=1
	.2byte 0x0002, 0x0002, 0x0002, 0x0002, 0x0807
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A984D4:
	.if (.L_end_gUnknown_08A984D4 - gUnknown_08A984D4) != 56
	.error "gUnknown_08A984D4 size mismatch"
	.endif

	.global gUnknown_08A9850C
gUnknown_08A9850C:  @ 0x8A9850C
	.byte 44, 15, 2, 1  @ rect 0: x=44 y=15 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 44, 16, 2, 1  @ rect 1: x=44 y=16 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 44, 17, 2, 1  @ rect 2: x=44 y=17 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 44, 18, 2, 1  @ rect 3: x=44 y=18 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 44, 19, 2, 1  @ rect 4: x=44 y=19 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 44, 20, 2, 1  @ rect 5: x=44 y=20 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A9850C:
	.if (.L_end_gUnknown_08A9850C - gUnknown_08A9850C) != 52
	.error "gUnknown_08A9850C size mismatch"
	.endif

	.global gUnknown_08A98540
gUnknown_08A98540:  @ 0x8A98540
	.byte 47, 11, 2, 1  @ rect 0: x=47 y=11 w=2 h=1
	.2byte 0x0407, 0x0406
	.byte 46, 12, 3, 1  @ rect 1: x=46 y=12 w=3 h=1
	.2byte 0x0407, 0x0406, 0x0807
	.byte 45, 13, 3, 1  @ rect 2: x=45 y=13 w=3 h=1
	.2byte 0x0407, 0x0406, 0x0807
	.byte 44, 14, 3, 1  @ rect 3: x=44 y=14 w=3 h=1
	.2byte 0x0407, 0x0406, 0x0807
	.byte 0xFF  @ terminator
	.byte 0x00  @ align pad
.L_end_gUnknown_08A98540:
	.if (.L_end_gUnknown_08A98540 - gUnknown_08A98540) != 40
	.error "gUnknown_08A98540 size mismatch"
	.endif

	.global gUnknown_08A98568
gUnknown_08A98568:  @ 0x8A98568
	.byte 42, 8, 6, 1  @ rect 0: x=42 y=8 w=6 h=1
	.2byte 0x0407, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001
	.byte 42, 9, 6, 1  @ rect 1: x=42 y=9 w=6 h=1
	.2byte 0x0003, 0x0405, 0x0002, 0x0002, 0x0002, 0x0002
	.byte 40, 10, 4, 1  @ rect 2: x=40 y=10 w=4 h=1
	.2byte 0x0001, 0x0001, 0x0805, 0x0004
	.byte 40, 11, 4, 1  @ rect 3: x=40 y=11 w=4 h=1
	.2byte 0x0002, 0x0002, 0x0002, 0x0807
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98568:
	.if (.L_end_gUnknown_08A98568 - gUnknown_08A98568) != 60
	.error "gUnknown_08A98568 size mismatch"
	.endif

	.global gUnknown_08A985A4
gUnknown_08A985A4:  @ 0x8A985A4
	.byte 48, 11, 3, 1  @ rect 0: x=48 y=11 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 49, 12, 3, 1  @ rect 1: x=49 y=12 w=3 h=1
	.2byte 0x0C07, 0x0006, 0x0007
	.byte 50, 13, 2, 1  @ rect 2: x=50 y=13 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 50, 14, 2, 1  @ rect 3: x=50 y=14 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 50, 15, 2, 1  @ rect 4: x=50 y=15 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 50, 16, 2, 1  @ rect 5: x=50 y=16 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 50, 17, 2, 1  @ rect 6: x=50 y=17 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 50, 18, 2, 1  @ rect 7: x=50 y=18 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 50, 19, 2, 1  @ rect 8: x=50 y=19 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 50, 20, 2, 1  @ rect 9: x=50 y=20 w=2 h=1
	.2byte 0x0003, 0x0004
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A985A4:
	.if (.L_end_gUnknown_08A985A4 - gUnknown_08A985A4) != 88
	.error "gUnknown_08A985A4 size mismatch"
	.endif

	.global gUnknown_08A985FC
gUnknown_08A985FC:  @ 0x8A985FC
	.incbin "graphics/misc/gUnknown_08A985FC.4bpp"

	.global gUnknown_08A9863C
gUnknown_08A9863C:  @ 0x8A9863C
	.byte 32, 4, 11, 1  @ rect 0: x=32 y=4 w=11 h=1
	.2byte 0x040E, 0x0008, 0x0008, 0x0008, 0x0008, 0x0008, 0x0008, 0x0008
	.2byte 0x0008, 0x0008, 0x0008
	.byte 31, 5, 12, 1  @ rect 1: x=31 y=5 w=12 h=1
	.2byte 0x040E, 0x040D, 0x0009, 0x0009, 0x0009, 0x0009, 0x0009, 0x0009
	.2byte 0x0009, 0x0009, 0x0009, 0x0009
	.byte 27, 6, 6, 1  @ rect 2: x=27 y=6 w=6 h=1
	.2byte 0x0008, 0x0008, 0x0008, 0x0008, 0x040D, 0x080E
	.byte 27, 7, 5, 1  @ rect 3: x=27 y=7 w=5 h=1
	.2byte 0x0009, 0x0009, 0x0009, 0x0009, 0x080E
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A9863C:
	.if (.L_end_gUnknown_08A9863C - gUnknown_08A9863C) != 88
	.error "gUnknown_08A9863C size mismatch"
	.endif

	.global gUnknown_08A98694
gUnknown_08A98694:  @ 0x8A98694
	.byte 42, 8, 2, 1  @ rect 0: x=42 y=8 w=2 h=1
	.2byte 0x0407, 0x0001
	.byte 42, 9, 2, 1  @ rect 1: x=42 y=9 w=2 h=1
	.2byte 0x0003, 0x0405
	.byte 40, 10, 4, 1  @ rect 2: x=40 y=10 w=4 h=1
	.2byte 0x0001, 0x0001, 0x0805, 0x0004
	.byte 40, 11, 4, 1  @ rect 3: x=40 y=11 w=4 h=1
	.2byte 0x0002, 0x0002, 0x0002, 0x0807
	.byte 0xFF  @ terminator
	.byte 0x00, 0x00, 0x00  @ align pad
.L_end_gUnknown_08A98694:
	.if (.L_end_gUnknown_08A98694 - gUnknown_08A98694) != 44
	.error "gUnknown_08A98694 size mismatch"
	.endif

	.global gUnknown_08A986C0
gUnknown_08A986C0:  @ 0x08A986C0
	.incbin "graphics/misc/gUnknown_08A986C0.4bpp.lz"

	.global gUnknown_08A98BF8
gUnknown_08A98BF8:  @ 0x08A98BF8
	.incbin "graphics/misc/gUnknown_08A98BF8.map.bin.lz"

	.global gUnknown_08A98CFC
gUnknown_08A98CFC:  @ 0x08A98CFC
	.incbin "graphics/misc/gUnknown_08A98CFC.map.bin"

	.global gUnknown_08A98D58
gUnknown_08A98D58:  @ 0x08A98D58
	.incbin "graphics/misc/gUnknown_08A98D58.map.bin"

	.global gUnknown_08A98D88
gUnknown_08A98D88:  @ 0x08A98D88
	.incbin "graphics/misc/gUnknown_08A98D88.map.bin"

	.global gUnknown_08A98DA0
gUnknown_08A98DA0:  @ 0x08A98DA0
	.incbin "graphics/misc/gUnknown_08A98DA0.map.bin"

	.global gUnknown_08A98DB8
gUnknown_08A98DB8:  @ 0x08A98DB8
	.incbin "graphics/misc/gUnknown_08A98DB8.map.bin"

	.global gUnknown_08A98DCC
gUnknown_08A98DCC:  @ 0x08A98DCC
	.incbin "graphics/misc/gUnknown_08A98DCC.4bpp"

	.global gUnknown_08A98E2C
gUnknown_08A98E2C:  @ 0x08A98E2C
	.incbin "graphics/misc/gUnknown_08A98E2C.4bpp"

	.global gUnknown_08A98E4C
gUnknown_08A98E4C:  @ 0x08A98E4C
    .incbin "graphics/misc/gUnknown_08A98E4C.4bpp"

	.global gUnknown_08A98E6C
gUnknown_08A98E6C:  @ 0x08A98E6C
	.incbin "graphics/misc/gUnknown_08A98E6C.4bpp"

	.global gUnknown_08A98E8C
gUnknown_08A98E8C:  @ 0x08A98E8C
	.incbin "graphics/misc/gUnknown_08A98E8C.4bpp"

	.global gUnknown_08A98EAC
gUnknown_08A98EAC:  @ 0x08A98EAC
	.incbin "graphics/misc/gUnknown_08A98EAC.map.bin"

	.global gUnknown_08A98F30
gUnknown_08A98F30:  @ 0x08A98F30
	.incbin "graphics/misc/gUnknown_08A98F30.map.bin"

	.global gGfx_GMapPI_LevelNums
gGfx_GMapPI_LevelNums:  @ 0x08A9901C
	.incbin "graphics/misc/gGfx_GMapPI_LevelNums.4bpp.lz"

	.global gPal_GMapPI_LevelNums
gPal_GMapPI_LevelNums:  @ 0x08A99120
	.incbin "graphics/misc/gPal_GMapPI_LevelNums.gbapal"

	.global Img_EventGmap
Img_EventGmap:  @ 0x08A99140
	.incbin "graphics/misc/Img_EventGmap.4bpp.lz"

	.global Tsa_EventGmap
Tsa_EventGmap:  @ 0x08A9DF74
	.incbin "graphics/misc/Tsa_EventGmap.map.bin.lz"

	.global Pal_EventGmap
Pal_EventGmap:  @ 0x08A9E4C4
	.incbin "graphics/misc/Pal_EventGmap.gbapal"

	.global gImg_08A9E544
gImg_08A9E544:  @ 0x08A9E544
	.incbin "graphics/misc/gImg_08A9E544.4bpp.lz"

	.global gPal_08A9E5BC
gPal_08A9E5BC:  @ 0x08A9E5BC
	.incbin "graphics/misc/gPal_08A9E5BC.gbapal"

	.global gTsa_08A9E5DC
gTsa_08A9E5DC:  @ 0x08A9E5DC
	.incbin "graphics/misc/gTsa_08A9E5DC.map.bin.lz"

	.global Pal_WmHighLightNationMap
Pal_WmHighLightNationMap:  @ 0x08A9E688
	.incbin "graphics/misc/Pal_WmHighLightNationMap.gbapal"

    .global Img_WmHightLightMapFrecia
Img_WmHightLightMapFrecia:
    .incbin "graphics/world_map/nations/Gfx_WmNationFrecia.4bpp.lz"

    .global Ap_WmHightLightMapFrecia
Ap_WmHightLightMapFrecia:
Ap_WmHightLightMapFrecia_motion:
	.2byte (Ap_WmHightLightMapFrecia_frame_list - Ap_WmHightLightMapFrecia_motion), (Ap_WmHightLightMapFrecia_anim_list - Ap_WmHightLightMapFrecia_motion) @ header

Ap_WmHightLightMapFrecia_frame_list: @ +$4
	.2byte (Ap_WmHightLightMapFrecia_frame_0 - Ap_WmHightLightMapFrecia_frame_list)
	.2byte (Ap_WmHightLightMapFrecia_frame_1 - Ap_WmHightLightMapFrecia_frame_list)

Ap_WmHightLightMapFrecia_anim_list: @ +$8
	.2byte (Ap_WmHightLightMapFrecia_anim_0 - Ap_WmHightLightMapFrecia_anim_list)
	.2byte (Ap_WmHightLightMapFrecia_anim_1 - Ap_WmHightLightMapFrecia_anim_list)

Ap_WmHightLightMapFrecia_frame_0: @ +$C
	.2byte 10 @ oam entries
	.2byte 0x40DF, 0xC1C9, 0x0 @ OAM Data #0
	.2byte 0xDF, 0x8009, 0x8 @ OAM Data #1
	.2byte 0x40FF, 0xC1D1, 0xE @ OAM Data #2
	.2byte 0x401F, 0x81D9, 0x1A @ OAM Data #3
	.2byte 0x801F, 0x1F9, 0x1E @ OAM Data #4
	.2byte 0x40FF, 0x8011, 0x16 @ OAM Data #5
	.2byte 0xEF, 0x4029, 0x4C @ OAM Data #6
	.2byte 0xE7, 0x29, 0x2C @ OAM Data #7
	.2byte 0xF, 0x11, 0xC @ OAM Data #8
	.2byte 0x1F, 0x1D1, 0x1F @ OAM Data #9

Ap_WmHightLightMapFrecia_frame_1: @ +$4A
	.2byte 3 @ oam entries
	.2byte 0x40F9, 0x81E6, 0x56 @ OAM Data #0
	.2byte 0xF9, 0x4006, 0x5A @ OAM Data #1
	.2byte 0x80F9, 0x16, 0x5C @ OAM Data #2

Ap_WmHightLightMapFrecia_anim_0: @ +$5E
	.2byte  4,  0

	.2byte 0, (-1) @ loop back to start

Ap_WmHightLightMapFrecia_anim_1: @ +$66
	.2byte  4,  1

	.2byte 0, (-1) @ loop back to start

	.byte 0x00, 0x00  @ trailing anim data not decoded by apdump
.L_end_Ap_WmHightLightMapFrecia:
	.if (.L_end_Ap_WmHightLightMapFrecia - Ap_WmHightLightMapFrecia) != 112
	.error "Ap_WmHightLightMapFrecia size mismatch"
	.endif

    .global Img_WmHightLightMap2
Img_WmHightLightMap2:
    .incbin "graphics/misc/Img_WmHightLightMap2.4bpp.lz"

    .global Ap_WmHightLightMap2
Ap_WmHightLightMap2:
Ap_WmHightLightMap2_motion:
	.2byte (Ap_WmHightLightMap2_frame_list - Ap_WmHightLightMap2_motion), (Ap_WmHightLightMap2_anim_list - Ap_WmHightLightMap2_motion) @ header

Ap_WmHightLightMap2_frame_list: @ +$4
	.2byte (Ap_WmHightLightMap2_frame_0 - Ap_WmHightLightMap2_frame_list)
	.2byte (Ap_WmHightLightMap2_frame_1 - Ap_WmHightLightMap2_frame_list)

Ap_WmHightLightMap2_anim_list: @ +$8
	.2byte (Ap_WmHightLightMap2_anim_0 - Ap_WmHightLightMap2_anim_list)
	.2byte (Ap_WmHightLightMap2_anim_1 - Ap_WmHightLightMap2_anim_list)

Ap_WmHightLightMap2_frame_0: @ +$C
	.2byte 41 @ oam entries
	.2byte 0x40F8, 0x81B7, 0x40 @ OAM Data #0
	.2byte 0xF8, 0x41F7, 0x48 @ OAM Data #1
	.2byte 0x80F8, 0x27, 0x9 @ OAM Data #2
	.2byte 0x4018, 0x41CF, 0x50 @ OAM Data #3
	.2byte 0x4018, 0x81EF, 0x54 @ OAM Data #4
	.2byte 0x4018, 0x800F, 0x58 @ OAM Data #5
	.2byte 0x4008, 0x81B7, 0x10 @ OAM Data #6
	.2byte 0x4008, 0x81D7, 0x14 @ OAM Data #7
	.2byte 0x8, 0x403F, 0x1E @ OAM Data #8
	.2byte 0x40D8, 0x81BF, 0x18 @ OAM Data #9
	.2byte 0xD8, 0x41DF, 0x1C @ OAM Data #10
	.2byte 0x40E0, 0x41EF, 0x70 @ OAM Data #11
	.2byte 0xC8, 0x41D7, 0x4A @ OAM Data #12
	.2byte 0xE8, 0x41D7, 0x4 @ OAM Data #13
	.2byte 0xE8, 0x41E7, 0x6 @ OAM Data #14
	.2byte 0xE8, 0x4007, 0xA @ OAM Data #15
	.2byte 0xE8, 0x4017, 0xC @ OAM Data #16
	.2byte 0x40F0, 0x27, 0x2E @ OAM Data #17
	.2byte 0xE8, 0x41F7, 0x6 @ OAM Data #18
	.2byte 0xF8, 0x4007, 0x6 @ OAM Data #19
	.2byte 0xF8, 0x4017, 0x6 @ OAM Data #20
	.2byte 0x80E8, 0x1BF, 0x1 @ OAM Data #21
	.2byte 0xF0, 0x1B7, 0x20 @ OAM Data #22
	.2byte 0xE8, 0x41C7, 0x2 @ OAM Data #23
	.2byte 0x8, 0x41FF, 0x6 @ OAM Data #24
	.2byte 0x8, 0x400F, 0x6 @ OAM Data #25
	.2byte 0x8, 0x401F, 0x6 @ OAM Data #26
	.2byte 0x8, 0x402F, 0x6 @ OAM Data #27
	.2byte 0x18, 0x402F, 0x5C @ OAM Data #28
	.2byte 0x8018, 0x3F, 0x5E @ OAM Data #29
	.2byte 0x18, 0x47, 0x5F @ OAM Data #30
	.2byte 0xF0, 0x37, 0x7F @ OAM Data #31
	.2byte 0xF8, 0x402F, 0x4C @ OAM Data #32
	.2byte 0x4000, 0x3F, 0x6E @ OAM Data #33
	.2byte 0xF8, 0x3F, 0x4E @ OAM Data #34
	.2byte 0x18, 0x1C7, 0xE @ OAM Data #35
	.2byte 0xD0, 0x1CF, 0xF @ OAM Data #36
	.2byte 0x8008, 0x1F7, 0x8 @ OAM Data #37
	.2byte 0xF8, 0x41E7, 0x46 @ OAM Data #38
	.2byte 0x40F8, 0x1D7, 0x44 @ OAM Data #39
	.2byte 0x4028, 0x2F, 0x64 @ OAM Data #40

Ap_WmHightLightMap2_frame_1: @ +$104
	.2byte 6 @ oam entries
	.2byte 0x40F9, 0x41E7, 0x80 @ OAM Data #0
	.2byte 0x40F9, 0x7, 0x84 @ OAM Data #1
	.2byte 0xF9, 0x17, 0x86 @ OAM Data #2
	.2byte 0x4001, 0x41E7, 0x87 @ OAM Data #3
	.2byte 0x4001, 0x7, 0x8B @ OAM Data #4
	.2byte 0x1, 0x17, 0x8D @ OAM Data #5

Ap_WmHightLightMap2_anim_0: @ +$12A
	.2byte  4,  0

	.2byte 0, (-1) @ loop back to start

Ap_WmHightLightMap2_anim_1: @ +$132
	.2byte  4,  1

	.2byte 0, (-1) @ loop back to start

	.byte 0x00, 0x00  @ trailing anim data not decoded by apdump
.L_end_Ap_WmHightLightMap2:
	.if (.L_end_Ap_WmHightLightMap2 - Ap_WmHightLightMap2) != 316
	.error "Ap_WmHightLightMap2 size mismatch"
	.endif

    .global Img_WmHightLightMap3
Img_WmHightLightMap3:
    .incbin "graphics/misc/Img_WmHightLightMap3.4bpp.lz"

    .global Ap_WmHightLightMap3
Ap_WmHightLightMap3:
Ap_WmHightLightMap3_motion:
	.2byte (Ap_WmHightLightMap3_frame_list - Ap_WmHightLightMap3_motion), (Ap_WmHightLightMap3_anim_list - Ap_WmHightLightMap3_motion) @ header

Ap_WmHightLightMap3_frame_list: @ +$4
	.2byte (Ap_WmHightLightMap3_frame_0 - Ap_WmHightLightMap3_frame_list)
	.2byte (Ap_WmHightLightMap3_frame_1 - Ap_WmHightLightMap3_frame_list)

Ap_WmHightLightMap3_anim_list: @ +$8
	.2byte (Ap_WmHightLightMap3_anim_0 - Ap_WmHightLightMap3_anim_list)
	.2byte (Ap_WmHightLightMap3_anim_1 - Ap_WmHightLightMap3_anim_list)

Ap_WmHightLightMap3_frame_0: @ +$C
	.2byte 8 @ oam entries
	.2byte 0x40EB, 0xC1D9, 0x0 @ OAM Data #0
	.2byte 0x80EB, 0x8019, 0x8 @ OAM Data #1
	.2byte 0x80EB, 0x4029, 0xA @ OAM Data #2
	.2byte 0x400B, 0xC1D9, 0xB @ OAM Data #3
	.2byte 0x40DB, 0x81E1, 0x15 @ OAM Data #4
	.2byte 0x40DB, 0x8001, 0x19 @ OAM Data #5
	.2byte 0xB, 0x4019, 0x13 @ OAM Data #6
	.2byte 0xFB, 0x31, 0x1D @ OAM Data #7

Ap_WmHightLightMap3_frame_1: @ +$3E
	.2byte 3 @ oam entries
	.2byte 0x40F8, 0x81DF, 0x53 @ OAM Data #0
	.2byte 0x40F8, 0x81FF, 0x57 @ OAM Data #1
	.2byte 0x80F8, 0x1F, 0x5B @ OAM Data #2

Ap_WmHightLightMap3_anim_0: @ +$52
	.2byte  4,  0

	.2byte 0, (-1) @ loop back to start

Ap_WmHightLightMap3_anim_1: @ +$5A
	.2byte  4,  1

	.2byte 0, (-1) @ loop back to start

	.byte 0x00, 0x00  @ trailing anim data not decoded by apdump
.L_end_Ap_WmHightLightMap3:
	.if (.L_end_Ap_WmHightLightMap3 - Ap_WmHightLightMap3) != 100
	.error "Ap_WmHightLightMap3 size mismatch"
	.endif

    .global Img_WmHightLightMap4
Img_WmHightLightMap4:
    .incbin "graphics/misc/Img_WmHightLightMap4.4bpp.lz"

    .global Ap_WmHightLightMap4
Ap_WmHightLightMap4:
Ap_WmHightLightMap4_motion:
	.2byte (Ap_WmHightLightMap4_frame_list - Ap_WmHightLightMap4_motion), (Ap_WmHightLightMap4_anim_list - Ap_WmHightLightMap4_motion) @ header

Ap_WmHightLightMap4_frame_list: @ +$4
	.2byte (Ap_WmHightLightMap4_frame_0 - Ap_WmHightLightMap4_frame_list)
	.2byte (Ap_WmHightLightMap4_frame_1 - Ap_WmHightLightMap4_frame_list)

Ap_WmHightLightMap4_anim_list: @ +$8
	.2byte (Ap_WmHightLightMap4_anim_0 - Ap_WmHightLightMap4_anim_list)
	.2byte (Ap_WmHightLightMap4_anim_1 - Ap_WmHightLightMap4_anim_list)

Ap_WmHightLightMap4_frame_0: @ +$C
	.2byte 8 @ oam entries
	.2byte 0xDD, 0x81DD, 0x0 @ OAM Data #0
	.2byte 0x80FD, 0x8005, 0xA @ OAM Data #1
	.2byte 0x80FD, 0x4015, 0xC @ OAM Data #2
	.2byte 0xDD, 0x81DD, 0x0 @ OAM Data #3
	.2byte 0xED, 0x41FD, 0x44 @ OAM Data #4
	.2byte 0x40F5, 0xD, 0x66 @ OAM Data #5
	.2byte 0x40FD, 0x81E5, 0x6 @ OAM Data #6
	.2byte 0x400D, 0x1F5, 0x48 @ OAM Data #7

Ap_WmHightLightMap4_frame_1: @ +$3E
	.2byte 2 @ oam entries
	.2byte 0x40F8, 0x81E0, 0xD @ OAM Data #0
	.2byte 0x40F8, 0x8000, 0x11 @ OAM Data #1

Ap_WmHightLightMap4_anim_0: @ +$4C
	.2byte  4,  0

	.2byte 0, (-1) @ loop back to start

Ap_WmHightLightMap4_anim_1: @ +$54
	.2byte  4,  1

	.2byte 0, (-1) @ loop back to start

.L_end_Ap_WmHightLightMap4:
	.if (.L_end_Ap_WmHightLightMap4 - Ap_WmHightLightMap4) != 92
	.error "Ap_WmHightLightMap4 size mismatch"
	.endif

    .global Img_WmHightLightMap5
Img_WmHightLightMap5:
    .incbin "graphics/misc/Img_WmHightLightMap5.4bpp.lz"

    .global Ap_WmHightLightMap5
Ap_WmHightLightMap5:
Ap_WmHightLightMap5_motion:
	.2byte (Ap_WmHightLightMap5_frame_list - Ap_WmHightLightMap5_motion), (Ap_WmHightLightMap5_anim_list - Ap_WmHightLightMap5_motion) @ header

Ap_WmHightLightMap5_frame_list: @ +$4
	.2byte (Ap_WmHightLightMap5_frame_0 - Ap_WmHightLightMap5_frame_list)
	.2byte (Ap_WmHightLightMap5_frame_1 - Ap_WmHightLightMap5_frame_list)

Ap_WmHightLightMap5_anim_list: @ +$8
	.2byte (Ap_WmHightLightMap5_anim_0 - Ap_WmHightLightMap5_anim_list)
	.2byte (Ap_WmHightLightMap5_anim_1 - Ap_WmHightLightMap5_anim_list)

Ap_WmHightLightMap5_frame_0: @ +$C
	.2byte 2 @ oam entries
	.2byte 0xF0, 0x81E9, 0x0 @ OAM Data #0
	.2byte 0x80F0, 0x4009, 0x4 @ OAM Data #1

Ap_WmHightLightMap5_frame_1: @ +$1A
	.2byte 1 @ oam entries
	.2byte 0xF9, 0x41F9, 0x5 @ OAM Data #0

Ap_WmHightLightMap5_anim_0: @ +$22
	.2byte  4,  0

	.2byte 0, (-1) @ loop back to start

Ap_WmHightLightMap5_anim_1: @ +$2A
	.2byte  4,  1

	.2byte 0, (-1) @ loop back to start

	.byte 0x00, 0x00  @ trailing anim data not decoded by apdump
.L_end_Ap_WmHightLightMap5:
	.if (.L_end_Ap_WmHightLightMap5 - Ap_WmHightLightMap5) != 52
	.error "Ap_WmHightLightMap5 size mismatch"
	.endif

    .global Img_WmHightLightMap6
Img_WmHightLightMap6:
    .incbin "graphics/misc/Img_WmHightLightMap6.4bpp.lz"

    .global Ap_WmHightLightMap6
Ap_WmHightLightMap6:
Ap_WmHightLightMap6_motion:
	.2byte (Ap_WmHightLightMap6_frame_list - Ap_WmHightLightMap6_motion), (Ap_WmHightLightMap6_anim_list - Ap_WmHightLightMap6_motion) @ header

Ap_WmHightLightMap6_frame_list: @ +$4
	.2byte (Ap_WmHightLightMap6_frame_0 - Ap_WmHightLightMap6_frame_list)
	.2byte (Ap_WmHightLightMap6_frame_1 - Ap_WmHightLightMap6_frame_list)

Ap_WmHightLightMap6_anim_list: @ +$8
	.2byte (Ap_WmHightLightMap6_anim_0 - Ap_WmHightLightMap6_anim_list)
	.2byte (Ap_WmHightLightMap6_anim_1 - Ap_WmHightLightMap6_anim_list)

Ap_WmHightLightMap6_frame_0: @ +$C
	.2byte 5 @ oam entries
	.2byte 0x40E4, 0xC1DB, 0x0 @ OAM Data #0
	.2byte 0x80E4, 0x801B, 0x8 @ OAM Data #1
	.2byte 0x80E4, 0x402B, 0xA @ OAM Data #2
	.2byte 0x4004, 0xC1E3, 0xB @ OAM Data #3
	.2byte 0x8004, 0x8023, 0x13 @ OAM Data #4

Ap_WmHightLightMap6_frame_1: @ +$2C
	.2byte 3 @ oam entries
	.2byte 0x40F8, 0x81DE, 0x15 @ OAM Data #0
	.2byte 0x40F8, 0x81FE, 0x19 @ OAM Data #1
	.2byte 0x80F8, 0x1E, 0x1D @ OAM Data #2

Ap_WmHightLightMap6_anim_0: @ +$40
	.2byte  4,  0

	.2byte 0, (-1) @ loop back to start

Ap_WmHightLightMap6_anim_1: @ +$48
	.2byte  4,  1

	.2byte 0, (-1) @ loop back to start

.L_end_Ap_WmHightLightMap6:
	.if (.L_end_Ap_WmHightLightMap6 - Ap_WmHightLightMap6) != 80
	.error "Ap_WmHightLightMap6 size mismatch"
	.endif

    .global Img_WmHightLightMap7
Img_WmHightLightMap7:
    .incbin "graphics/misc/Img_WmHightLightMap7.4bpp.lz"

    .global Ap_WmHightLightMap7
Ap_WmHightLightMap7:
Ap_WmHightLightMap7_motion:
	.2byte (Ap_WmHightLightMap7_frame_list - Ap_WmHightLightMap7_motion), (Ap_WmHightLightMap7_anim_list - Ap_WmHightLightMap7_motion) @ header

Ap_WmHightLightMap7_frame_list: @ +$4
	.2byte (Ap_WmHightLightMap7_frame_0 - Ap_WmHightLightMap7_frame_list)
	.2byte (Ap_WmHightLightMap7_frame_1 - Ap_WmHightLightMap7_frame_list)

Ap_WmHightLightMap7_anim_list: @ +$8
	.2byte (Ap_WmHightLightMap7_anim_0 - Ap_WmHightLightMap7_anim_list)
	.2byte (Ap_WmHightLightMap7_anim_1 - Ap_WmHightLightMap7_anim_list)

Ap_WmHightLightMap7_frame_0: @ +$C
	.2byte 1 @ oam entries
	.2byte 0xF6, 0x41F9, 0x0 @ OAM Data #0

Ap_WmHightLightMap7_frame_1: @ +$14
	.2byte 3 @ oam entries
	.2byte 0x4008, 0x81D4, 0x2 @ OAM Data #0
	.2byte 0x4008, 0x81F4, 0x6 @ OAM Data #1
	.2byte 0x4008, 0x8014, 0xA @ OAM Data #2

Ap_WmHightLightMap7_anim_0: @ +$28
	.2byte  4,  0

	.2byte 0, (-1) @ loop back to start

Ap_WmHightLightMap7_anim_1: @ +$30
	.2byte  4,  1

	.2byte 0, (-1) @ loop back to start

.L_end_Ap_WmHightLightMap7:
	.if (.L_end_Ap_WmHightLightMap7 - Ap_WmHightLightMap7) != 56
	.error "Ap_WmHightLightMap7 size mismatch"
	.endif

    .global Img_WmHightLightMap8
Img_WmHightLightMap8:
    .incbin "graphics/misc/Img_WmHightLightMap8.4bpp.lz"

    .global Ap_WmHightLightMap8
Ap_WmHightLightMap8:
Ap_WmHightLightMap8_motion:
	.2byte (Ap_WmHightLightMap8_frame_list - Ap_WmHightLightMap8_motion), (Ap_WmHightLightMap8_anim_list - Ap_WmHightLightMap8_motion) @ header

Ap_WmHightLightMap8_frame_list: @ +$4
	.2byte (Ap_WmHightLightMap8_frame_0 - Ap_WmHightLightMap8_frame_list)
	.2byte (Ap_WmHightLightMap8_frame_1 - Ap_WmHightLightMap8_frame_list)

Ap_WmHightLightMap8_anim_list: @ +$8
	.2byte (Ap_WmHightLightMap8_anim_0 - Ap_WmHightLightMap8_anim_list)
	.2byte (Ap_WmHightLightMap8_anim_1 - Ap_WmHightLightMap8_anim_list)

Ap_WmHightLightMap8_frame_0: @ +$C
	.2byte 9 @ oam entries
	.2byte 0x40E2, 0xC1D3, 0x0 @ OAM Data #0
	.2byte 0x80E2, 0x8013, 0x8 @ OAM Data #1
	.2byte 0x4002, 0xC1D3, 0xA @ OAM Data #2
	.2byte 0x8002, 0x8013, 0x12 @ OAM Data #3
	.2byte 0x8002, 0x4023, 0x14 @ OAM Data #4
	.2byte 0x40E2, 0xC1D3, 0x0 @ OAM Data #5
	.2byte 0x80E2, 0x8013, 0x8 @ OAM Data #6
	.2byte 0x40E2, 0xC1D3, 0x0 @ OAM Data #7
	.2byte 0x80E2, 0x8013, 0x8 @ OAM Data #8

Ap_WmHightLightMap8_frame_1: @ +$44
	.2byte 3 @ oam entries
	.2byte 0x40F8, 0x81E6, 0x15 @ OAM Data #0
	.2byte 0xF8, 0x4006, 0x19 @ OAM Data #1
	.2byte 0x80F8, 0x16, 0x1B @ OAM Data #2

Ap_WmHightLightMap8_anim_0: @ +$58
	.2byte  4,  0

	.2byte 0, (-1) @ loop back to start

Ap_WmHightLightMap8_anim_1: @ +$60
	.2byte  4,  1

	.2byte 0, (-1) @ loop back to start

.L_end_Ap_WmHightLightMap8:
	.if (.L_end_Ap_WmHightLightMap8 - Ap_WmHightLightMap8) != 104
	.error "Ap_WmHightLightMap8 size mismatch"
	.endif

	.global Img_WorldMapPlaceDot
Img_WorldMapPlaceDot:  @ 0x08AA114C
	.incbin "graphics/misc/Img_WorldMapPlaceDot.4bpp.lz"

	.global Pal_WmPlaceDot_Highlight
Pal_WmPlaceDot_Highlight:  @ 0x08AA1190
	.incbin "graphics/misc/Pal_WmPlaceDot_Highlight.gbapal"

	.global Pal_WmPlaceDot_Standard
Pal_WmPlaceDot_Standard:  @ 0x08AA11B0
	.incbin "graphics/misc/Pal_WmPlaceDot_Standard.gbapal"

	.global gUnknown_08AA11D0
gUnknown_08AA11D0:  @ 0x08AA11D0
	.incbin "graphics/misc/gUnknown_08AA11D0.4bpp.lz"

	.global Img_WorldmapMinimap
Img_WorldmapMinimap:  @ 0x08AA1280
	.incbin "graphics/misc/Img_WorldmapMinimap.4bpp.lz"

	.global Pal_WorldmapMinimap
Pal_WorldmapMinimap:  @ 0x08AA188C
	.incbin "graphics/misc/Pal_WorldmapMinimap.gbapal"

	.global gUnknown_08AA18AC
gUnknown_08AA18AC:  @ 0x08AA18AC
	.incbin "graphics/misc/gUnknown_08AA18AC.map.bin"

	.global gUnknown_08AA1930
gUnknown_08AA1930:  @ 0x08AA1930
	.incbin "graphics/misc/gUnknown_08AA1930.4bpp"

	.global gUnknown_08AA1950
gUnknown_08AA1950:  @ 0x08AA1950
	.incbin "graphics/misc/gUnknown_08AA1950.4bpp"

	.global gImg_WorldmapSkirmish
gImg_WorldmapSkirmish:  @ 0x08AA1970
	.incbin "graphics/misc/gImg_WorldmapSkirmish.4bpp.lz"

	.global SpriteAnim_WorldmapSkirmish
SpriteAnim_WorldmapSkirmish:  @ 0x08AA1C70
SpriteAnim_WorldmapSkirmish_motion:
	.2byte (SpriteAnim_WorldmapSkirmish_frame_list - SpriteAnim_WorldmapSkirmish_motion), (SpriteAnim_WorldmapSkirmish_anim_list - SpriteAnim_WorldmapSkirmish_motion) @ header

SpriteAnim_WorldmapSkirmish_frame_list: @ +$4
	.2byte (SpriteAnim_WorldmapSkirmish_frame_0 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_1 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_2 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_3 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_4 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_5 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_6 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_7 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_8 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_9 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_10 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_11 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_12 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_13 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_14 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_15 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_16 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_17 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_18 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_19 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_20 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_21 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_22 - SpriteAnim_WorldmapSkirmish_frame_list)
	.2byte (SpriteAnim_WorldmapSkirmish_frame_23 - SpriteAnim_WorldmapSkirmish_frame_list)

SpriteAnim_WorldmapSkirmish_anim_list: @ +$34
	.2byte (SpriteAnim_WorldmapSkirmish_anim_0 - SpriteAnim_WorldmapSkirmish_anim_list)

SpriteAnim_WorldmapSkirmish_frame_0: @ +$36
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0x19
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81C9, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81E9, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x8009, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_1: @ +$52
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0x4C
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81CC, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81EC, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x800C, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_2: @ +$6E
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0x80
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81CE, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81EE, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x800E, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_3: @ +$8A
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0xB3
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81CF, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81EF, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x800F, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_4: @ +$A6
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0xE6
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81D0, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81F0, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x8010, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_5: @ +$C2
	.2byte 4 @ oam entries
	.2byte 0xF8, 0x1E8, 0x1013 @ OAM Data #0
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #1
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #2
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #3

SpriteAnim_WorldmapSkirmish_frame_6: @ +$DC
	.2byte 4 @ oam entries
	.2byte 0xF8, 0x1F0, 0x1032 @ OAM Data #0
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #1
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #2
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #3

SpriteAnim_WorldmapSkirmish_frame_7: @ +$F6
	.2byte 4 @ oam entries
	.2byte 0xF8, 0x0, 0x1031 @ OAM Data #0
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #1
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #2
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #3

SpriteAnim_WorldmapSkirmish_frame_8: @ +$110
	.2byte 4 @ oam entries
	.2byte 0xF8, 0x10, 0x1031 @ OAM Data #0
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #1
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #2
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #3

SpriteAnim_WorldmapSkirmish_frame_9: @ +$12A
	.2byte 4 @ oam entries
	.2byte 0xF8, 0x20, 0x1032 @ OAM Data #0
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #1
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #2
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #3

SpriteAnim_WorldmapSkirmish_frame_10: @ +$144
	.2byte 4 @ oam entries
	.2byte 0xF8, 0x28, 0x1013 @ OAM Data #0
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #1
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #2
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #3

SpriteAnim_WorldmapSkirmish_frame_11: @ +$15E
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x33, 0x33
	.2byte 4 @ oam entries
	.2byte 0x1EC, 0x8020, 0x1000 @ OAM Data #0
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #1
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #2
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #3

SpriteAnim_WorldmapSkirmish_frame_12: @ +$180
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x59, 0x59
	.2byte 7 @ oam entries
	.2byte 0xF8, 0x2C, 0x1031 @ OAM Data #0
	.2byte 0xFC, 0x2C, 0x1011 @ OAM Data #1
	.2byte 0xF4, 0x2C, 0x1011 @ OAM Data #2
	.2byte 0x1EC, 0x8020, 0x1000 @ OAM Data #3
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #4
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #5
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #6

SpriteAnim_WorldmapSkirmish_frame_13: @ +$1B4
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x80, 0x80
	.2byte 8 @ oam entries
	.2byte 0xF8, 0x34, 0x1031 @ OAM Data #0
	.2byte 0xF8, 0x24, 0x1031 @ OAM Data #1
	.2byte 0x80EC, 0x2C, 0x1010 @ OAM Data #2
	.2byte 0x80FC, 0x2C, 0x1010 @ OAM Data #3
	.2byte 0x1EC, 0x8020, 0x1000 @ OAM Data #4
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #5
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #6
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #7

SpriteAnim_WorldmapSkirmish_frame_14: @ +$1EE
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0xB3, 0xB3
	.2byte 8 @ oam entries
	.2byte 0xF8, 0x20, 0x1032 @ OAM Data #0
	.2byte 0xF8, 0x38, 0x1032 @ OAM Data #1
	.2byte 0x8, 0x2C, 0x1011 @ OAM Data #2
	.2byte 0xE8, 0x2C, 0x1011 @ OAM Data #3
	.2byte 0x1EC, 0x8020, 0x1000 @ OAM Data #4
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #5
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #6
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #7

SpriteAnim_WorldmapSkirmish_frame_15: @ +$228
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0xE6, 0xE6
	.2byte 8 @ oam entries
	.2byte 0xF8, 0x3A, 0x1013 @ OAM Data #0
	.2byte 0xF8, 0x1E, 0x1013 @ OAM Data #1
	.2byte 0x1EC, 0x8020, 0x1000 @ OAM Data #2
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #3
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #4
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #5
	.2byte 0xE4, 0x2C, 0x1012 @ OAM Data #6
	.2byte 0xC, 0x2C, 0x1012 @ OAM Data #7

SpriteAnim_WorldmapSkirmish_frame_16: @ +$262
	.2byte 8 @ oam entries
	.2byte 0xF8, 0x1C, 0x1013 @ OAM Data #0
	.2byte 0xEC, 0x8020, 0x1000 @ OAM Data #1
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #2
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #3
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #4
	.2byte 0xE2, 0x2C, 0x1013 @ OAM Data #5
	.2byte 0xE, 0x2C, 0x1013 @ OAM Data #6
	.2byte 0xF8, 0x3C, 0x1013 @ OAM Data #7

SpriteAnim_WorldmapSkirmish_frame_17: @ +$294
	.2byte 9 @ oam entries
	.2byte 0xEC, 0x4020, 0x1014 @ OAM Data #0
	.2byte 0xEC, 0x5030, 0x1014 @ OAM Data #1
	.2byte 0xFC, 0x7030, 0x1014 @ OAM Data #2
	.2byte 0xFC, 0x6020, 0x1014 @ OAM Data #3
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #4
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #5
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #6
	.2byte 0xE1, 0x2C, 0x1013 @ OAM Data #7
	.2byte 0xF, 0x2C, 0x1013 @ OAM Data #8

SpriteAnim_WorldmapSkirmish_frame_18: @ +$2CC
	.2byte 3 @ oam entries
	.2byte 0x40F0, 0x81D0, 0x1004 @ OAM Data #0
	.2byte 0x40F0, 0x81F0, 0x1008 @ OAM Data #1
	.2byte 0x40F0, 0x8010, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_19: @ +$2E0
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0xE6
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81D0, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81F0, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x8010, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_20: @ +$2FC
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0xB3
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81D1, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81F1, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x8011, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_21: @ +$318
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0x80
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81D2, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81F2, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x8012, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_22: @ +$334
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0x4C
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81D4, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81F4, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x8014, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_frame_23: @ +$350
	.2byte (1 | 0x8000) @ rotscale entries
	.2byte 0x100, 0x100, 0x19
	.2byte 3 @ oam entries
	.2byte 0x41F0, 0x81D7, 0x1004 @ OAM Data #0
	.2byte 0x41F0, 0x81F7, 0x1008 @ OAM Data #1
	.2byte 0x41F0, 0x8017, 0x100C @ OAM Data #2

SpriteAnim_WorldmapSkirmish_anim_0: @ +$36C
	.2byte  1,  0
	.2byte  1,  1
	.2byte  1,  2
	.2byte  2,  3
	.2byte  1,  4
	.2byte  1,  5
	.2byte  2,  6
	.2byte  1,  7
	.2byte  1,  8
	.2byte  1,  9
	.2byte  1, 10
	.2byte  2, 11
	.2byte  2, 12
	.2byte  2, 13
	.2byte  2, 14
	.2byte  2, 15
	.2byte  1, 16
	.2byte  1, 17
	.2byte 60, 18
	.2byte  1, 19
	.2byte  2, 20
	.2byte  1, 21
	.2byte  1, 22
	.2byte  1, 23

	.2byte 0, 1 @ kill animated object

	.byte 0x00, 0x00, 0xFF, 0xFF  @ trailing anim data not decoded by apdump
.L_end_SpriteAnim_WorldmapSkirmish:
	.if (.L_end_SpriteAnim_WorldmapSkirmish - SpriteAnim_WorldmapSkirmish) != 980
	.error "SpriteAnim_WorldmapSkirmish size mismatch"
	.endif
