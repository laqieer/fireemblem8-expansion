.section .data

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
