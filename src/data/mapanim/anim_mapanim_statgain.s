@ AP (object/sprite animation) definition(s). Offset tables computed via assembler
@ label arithmetic; OAM/anim data uses the macros in include/ap.inc. See include/ap.h
@ / src/ap.c. Byte-identical to the original data.

	.include "ap.inc"

	.data
	.global SpriteAnim_ManimStatGain
	.align 1, 0
	.type SpriteAnim_ManimStatGain, %object
SpriteAnim_ManimStatGain:
	.hword .LSpriteAnim_ManimStatGain_ftab - SpriteAnim_ManimStatGain, .LSpriteAnim_ManimStatGain_atab - SpriteAnim_ManimStatGain
.LSpriteAnim_ManimStatGain_ftab:
	.hword .LSpriteAnim_ManimStatGain_frame_0 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_1 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_2 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_3 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_4 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_5 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_6 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_7 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_8 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_9 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_10 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_11 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_12 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_13 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_14 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_15 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_16 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_17 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_18 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_19 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_20 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_21 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_22 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_23 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_24 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_25 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_26 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_27 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_28 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_29 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_30 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_31 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_32 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_33 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_34 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_35 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_36 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_37 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_38 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_39 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_40 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_41 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_42 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_43 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_44 - .LSpriteAnim_ManimStatGain_ftab
	.hword .LSpriteAnim_ManimStatGain_frame_45 - .LSpriteAnim_ManimStatGain_ftab
.LSpriteAnim_ManimStatGain_atab:
	.hword .LSpriteAnim_ManimStatGain_anim_0 - .LSpriteAnim_ManimStatGain_atab
	.hword .LSpriteAnim_ManimStatGain_anim_1 - .LSpriteAnim_ManimStatGain_atab
	.hword .LSpriteAnim_ManimStatGain_anim_2 - .LSpriteAnim_ManimStatGain_atab
	.hword .LSpriteAnim_ManimStatGain_anim_3 - .LSpriteAnim_ManimStatGain_atab
	.hword .LSpriteAnim_ManimStatGain_anim_4 - .LSpriteAnim_ManimStatGain_atab
	.hword .LSpriteAnim_ManimStatGain_anim_5 - .LSpriteAnim_ManimStatGain_atab
	.hword .LSpriteAnim_ManimStatGain_anim_6 - .LSpriteAnim_ManimStatGain_atab
.LSpriteAnim_ManimStatGain_frame_0:
	ap_objs 3
	ap_obj 0x00F6, 0x01F9, 0x0000
	ap_obj 0x00FA, 0x01FA, 0x0001
	ap_obj 0x00FC, 0x01FC, 0x0002
.LSpriteAnim_ManimStatGain_frame_1:
	ap_objs 5
	ap_obj 0x00F8, 0x01F9, 0x0001
	ap_obj 0x00FB, 0x01FB, 0x0002
	ap_obj 0x00ED, 0x0002, 0x0000
	ap_obj 0x00EF, 0x01FC, 0x0000
	ap_obj 0x00F3, 0x01F9, 0x0001
.LSpriteAnim_ManimStatGain_frame_2:
	ap_objs 6
	ap_obj 0x00F1, 0x01FB, 0x0001
	ap_obj 0x00FA, 0x01F9, 0x0002
	ap_obj 0x00F4, 0x000B, 0x0000
	ap_obj 0x00EE, 0x0006, 0x0000
	ap_obj 0x00EE, 0x01FF, 0x0001
	ap_obj 0x00F5, 0x01F9, 0x0001
.LSpriteAnim_ManimStatGain_frame_3:
	ap_objs 6
	ap_obj 0x00ED, 0x0000, 0x0001
	ap_obj 0x00F6, 0x01F9, 0x0002
	ap_obj 0x00FB, 0x000D, 0x0000
	ap_obj 0x00F4, 0x000B, 0x0000
	ap_obj 0x00EF, 0x0007, 0x0001
	ap_obj 0x00F1, 0x01FB, 0x0001
.LSpriteAnim_ManimStatGain_frame_4:
	ap_objs 6
	ap_obj 0x00F2, 0x0009, 0x0001
	ap_obj 0x00EF, 0x01FC, 0x0002
	ap_obj 0x0006, 0x0008, 0x0000
	ap_obj 0x00FF, 0x000D, 0x0000
	ap_obj 0x00F9, 0x000D, 0x0001
	ap_obj 0x00EE, 0x0002, 0x0001
.LSpriteAnim_ManimStatGain_frame_5:
	ap_objs 6
	ap_obj 0x00F9, 0x000D, 0x0001
	ap_obj 0x00EE, 0x0004, 0x0002
	ap_obj 0x000C, 0x01FE, 0x0000
	ap_obj 0x0008, 0x0008, 0x0000
	ap_obj 0x0002, 0x000D, 0x0001
	ap_obj 0x00F2, 0x0009, 0x0001
.LSpriteAnim_ManimStatGain_frame_6:
	ap_objs 6
	ap_obj 0x0002, 0x000D, 0x0001
	ap_obj 0x00F0, 0x0008, 0x0002
	ap_obj 0x000A, 0x01F0, 0x0000
	ap_obj 0x000D, 0x01FA, 0x0000
	ap_obj 0x000A, 0x0005, 0x0001
	ap_obj 0x00F8, 0x000D, 0x0001
.LSpriteAnim_ManimStatGain_frame_7:
	ap_objs 6
	ap_obj 0x000E, 0x0000, 0x0001
	ap_obj 0x00FD, 0x000D, 0x0002
	ap_obj 0x00FE, 0x01E8, 0x0000
	ap_obj 0x0008, 0x01EC, 0x0000
	ap_obj 0x000F, 0x01F4, 0x0001
	ap_obj 0x0007, 0x000A, 0x0001
.LSpriteAnim_ManimStatGain_frame_8:
	ap_objs 6
	ap_obj 0x000E, 0x01EF, 0x0001
	ap_obj 0x0008, 0x0008, 0x0002
	ap_obj 0x00EF, 0x01E7, 0x0000
	ap_obj 0x00FA, 0x01E4, 0x0000
	ap_obj 0x0005, 0x01E7, 0x0001
	ap_obj 0x0010, 0x01FC, 0x0001
.LSpriteAnim_ManimStatGain_frame_9:
	ap_objs 6
	ap_obj 0x0011, 0x01F9, 0x0002
	ap_obj 0x00F5, 0x01E4, 0x0001
	ap_obj 0x00EA, 0x01EA, 0x0001
	ap_obj 0x000D, 0x01EE, 0x0002
	ap_obj 0x00E3, 0x01F3, 0x0000
	ap_obj 0x0002, 0x01E5, 0x0001
.LSpriteAnim_ManimStatGain_frame_10:
	ap_objs 6
	ap_obj 0x000C, 0x01ED, 0x0002
	ap_obj 0x00EE, 0x01E7, 0x0001
	ap_obj 0x00E1, 0x01F9, 0x0001
	ap_obj 0x00E5, 0x01EF, 0x0001
	ap_obj 0x0004, 0x01E7, 0x0002
	ap_obj 0x00F9, 0x01E4, 0x0002
.LSpriteAnim_ManimStatGain_frame_11:
	ap_objs 2
	ap_obj 0x40F7, 0x01F8, 0x0040
	ap_obj 0x00F8, 0x01DB, 0x0003
.LSpriteAnim_ManimStatGain_frame_12:
	ap_objs 3
	ap_obj 0x00EF, 0x41F8, 0x0022
	ap_obj 0x40F8, 0x01D2, 0x0004
	ap_obj 0x00F8, 0x01E2, 0x0006
.LSpriteAnim_ManimStatGain_frame_13:
	ap_objs 3
	ap_obj 0x00EF, 0x41F8, 0x0024
	ap_obj 0x40F8, 0x41CC, 0x0007
	ap_obj 0x00F8, 0x01EC, 0x000B
.LSpriteAnim_ManimStatGain_frame_14:
	ap_objs 3
	ap_obj 0x00EC, 0x41F8, 0x0024
	ap_obj 0x40F8, 0x41C8, 0x000C
	ap_obj 0x40F8, 0x01E8, 0x0010
.LSpriteAnim_ManimStatGain_frame_15:
	ap_objs 3
	ap_obj 0x00E5, 0x41F8, 0x0022
	ap_obj 0x40F8, 0x41C8, 0x000C
	ap_obj 0x40F8, 0x01E8, 0x0010
.LSpriteAnim_ManimStatGain_frame_16:
	ap_objs 3
	ap_obj 0x40EA, 0x01F8, 0x0040
	ap_obj 0x40F8, 0x41C8, 0x000C
	ap_obj 0x40F8, 0x01E8, 0x0010
.LSpriteAnim_ManimStatGain_frame_17:
	ap_objs 3
	ap_obj 0x00E7, 0x41F8, 0x0022
	ap_obj 0x40F8, 0x41C8, 0x000C
	ap_obj 0x40F8, 0x01E8, 0x0010
.LSpriteAnim_ManimStatGain_frame_18:
	ap_objs 3
	ap_obj 0x00EF, 0x41F8, 0x0026
	ap_obj 0x40F8, 0x41C8, 0x000C
	ap_obj 0x40F8, 0x01E8, 0x0010
.LSpriteAnim_ManimStatGain_frame_19:
	ap_objs 3
	ap_obj 0x00EF, 0x41F8, 0x0028
	ap_obj 0x40F8, 0x41C8, 0x000C
	ap_obj 0x40F8, 0x01E8, 0x0010
.LSpriteAnim_ManimStatGain_frame_20:
	ap_objs 3
	ap_obj 0x00EF, 0x41F8, 0x002A
	ap_obj 0x40F8, 0x41C8, 0x000C
	ap_obj 0x40F8, 0x01E8, 0x0010
.LSpriteAnim_ManimStatGain_frame_21:
	ap_objs 1
	ap_obj 0x40F0, 0x21F8, 0x0040
.LSpriteAnim_ManimStatGain_frame_22:
	ap_objs 1
	ap_obj 0x00EA, 0x61F8, 0x0028
.LSpriteAnim_ManimStatGain_frame_23:
	ap_objs 1
	ap_obj 0x00EA, 0x61F8, 0x002A
.LSpriteAnim_ManimStatGain_frame_24:
	ap_objs 1
	ap_obj 0x00F0, 0x41F8, 0x002C
.LSpriteAnim_ManimStatGain_frame_25:
	ap_objs 1
	ap_obj 0x40F7, 0x01F8, 0x0040
.LSpriteAnim_ManimStatGain_frame_26:
	ap_objs 1
	ap_obj 0x00EF, 0x41F8, 0x0022
.LSpriteAnim_ManimStatGain_frame_27:
	ap_objs 1
	ap_obj 0x00EF, 0x41F8, 0x0024
.LSpriteAnim_ManimStatGain_frame_28:
	ap_objs 1
	ap_obj 0x00EC, 0x41F8, 0x0024
.LSpriteAnim_ManimStatGain_frame_29:
	ap_objs 1
	ap_obj 0x00E5, 0x41F8, 0x0022
.LSpriteAnim_ManimStatGain_frame_30:
	ap_objs 1
	ap_obj 0x40EA, 0x01F8, 0x0040
.LSpriteAnim_ManimStatGain_frame_31:
	ap_objs 1
	ap_obj 0x00E7, 0x41F8, 0x0022
.LSpriteAnim_ManimStatGain_frame_32:
	ap_objs 1
	ap_obj 0x00EF, 0x41F8, 0x0026
.LSpriteAnim_ManimStatGain_frame_33:
	ap_objs 1
	ap_obj 0x00EF, 0x41F8, 0x0028
.LSpriteAnim_ManimStatGain_frame_34:
	ap_objs 1
	ap_obj 0x00EF, 0x41F8, 0x002A
.LSpriteAnim_ManimStatGain_frame_35:
	ap_objs 0
.LSpriteAnim_ManimStatGain_frame_36:
	ap_objs 1
	ap_obj 0x40F8, 0x21F8, 0x0040
.LSpriteAnim_ManimStatGain_frame_37:
	ap_objs 1
	ap_obj 0x00F8, 0x61F8, 0x0022
.LSpriteAnim_ManimStatGain_frame_38:
	ap_objs 1
	ap_obj 0x00F8, 0x61F8, 0x0024
.LSpriteAnim_ManimStatGain_frame_39:
	ap_objs 1
	ap_obj 0x00FB, 0x61F8, 0x0024
.LSpriteAnim_ManimStatGain_frame_40:
	ap_objs 1
	ap_obj 0x0002, 0x61F8, 0x0022
.LSpriteAnim_ManimStatGain_frame_41:
	ap_objs 1
	ap_obj 0x4005, 0x21F8, 0x0040
.LSpriteAnim_ManimStatGain_frame_42:
	ap_objs 1
	ap_obj 0x0000, 0x61F8, 0x0022
.LSpriteAnim_ManimStatGain_frame_43:
	ap_objs 1
	ap_obj 0x00F8, 0x61F8, 0x0026
.LSpriteAnim_ManimStatGain_frame_44:
	ap_objs 1
	ap_obj 0x00F8, 0x61F8, 0x0028
.LSpriteAnim_ManimStatGain_frame_45:
	ap_objs 1
	ap_obj 0x00F8, 0x61F8, 0x002A
.LSpriteAnim_ManimStatGain_anim_0:
	ap_anim 2, 0
	ap_anim 2, 1
	ap_anim 2, 2
	ap_anim 2, 3
	ap_anim 2, 4
	ap_anim 2, 5
	ap_anim 2, 6
	ap_anim 2, 7
	ap_anim 2, 8
	ap_anim 2, 9
	ap_anim 2, 10
	ap_anim_delete
	.hword 0x0000
	.hword 0xFFFF
.LSpriteAnim_ManimStatGain_anim_1:
	ap_anim 1, 11
	ap_anim 1, 12
	ap_anim 1, 13
	ap_anim 2, 14
	ap_anim 2, 15
	ap_anim 3, 16
	ap_anim 2, 15
	ap_anim 2, 17
	ap_anim 1, 18
	ap_anim 24, 19
	ap_anim 8, 20
	ap_anim_end
	.hword 0x0000
	.hword 0xFFFF
.LSpriteAnim_ManimStatGain_anim_2:
	ap_anim 1, 21
	ap_anim 24, 22
	ap_anim 8, 23
	ap_anim_end
	.hword 0x0000
	.hword 0xFFFF
.LSpriteAnim_ManimStatGain_anim_3:
	ap_anim 15, 35
	ap_anim 1, 24
	ap_anim_end
	.hword 0x0000
	.hword 0xFFFF
.LSpriteAnim_ManimStatGain_anim_4:
	ap_anim 1, 36
	ap_anim 4, 24
	ap_anim_end
	.hword 0x0000
	.hword 0xFFFF
.LSpriteAnim_ManimStatGain_anim_5:
	ap_anim 1, 25
	ap_anim 1, 26
	ap_anim 1, 27
	ap_anim 2, 28
	ap_anim 2, 29
	ap_anim 3, 30
	ap_anim 2, 29
	ap_anim 2, 31
	ap_anim 1, 32
	ap_anim 24, 33
	ap_anim 8, 34
	ap_anim_end
	.hword 0x0000
	.hword 0xFFFF
.LSpriteAnim_ManimStatGain_anim_6:
	ap_anim 1, 36
	ap_anim 1, 37
	ap_anim 1, 38
	ap_anim 2, 39
	ap_anim 2, 40
	ap_anim 3, 41
	ap_anim 2, 40
	ap_anim 2, 42
	ap_anim 1, 43
	ap_anim 24, 44
	ap_anim 8, 45
	ap_anim_end
	.hword 0x0000
	.hword 0xFFFF
	.hword 0x0004
	.hword 0x0022
	.hword 0x0022
	.hword 0x0030
	.hword 0x003E
	.hword 0x004C
	.hword 0x005A
	.hword 0x008C
	.hword 0x00BE
	.hword 0x00F0
	.hword 0x0122
	.hword 0x0154
	.hword 0x0186
	.hword 0x01B8
	.hword 0x01EA
	.hword 0x021C
	.hword 0x024E
	.hword 0x0262
	.hword 0x0282
	.hword 0x0002
	.hword 0x4000
	.hword 0x01F9
	.hword 0x000D
	.hword 0x80FB
	.hword 0x01FD
	.hword 0x000A
	.hword 0x0002
	.hword 0x4000
	.hword 0x01F9
	.hword 0x002B
	.hword 0x80FA
	.hword 0x01FD
	.hword 0x000A
	.hword 0x0002
	.hword 0x4000
	.hword 0x01F9
	.hword 0x000B
	.hword 0x80F9
	.hword 0x01FD
	.hword 0x000A
	.hword 0x0002
	.hword 0x4000
	.hword 0x01F9
	.hword 0x000B
	.hword 0x80F8
	.hword 0x01FD
	.hword 0x000A
	.hword 0x0008
	.hword 0x00F8
	.hword 0x01FC
	.hword 0x000F
	.hword 0x00FA
	.hword 0x01FF
	.hword 0x000F
	.hword 0x00FA
	.hword 0x01FA
	.hword 0x000F
	.hword 0x00FF
	.hword 0x01FA
	.hword 0x000F
	.hword 0x0001
	.hword 0x01FC
	.hword 0x000F
	.hword 0x00FF
	.hword 0x01FF
	.hword 0x000F
	.hword 0x00FC
	.hword 0x0001
	.hword 0x000F
	.hword 0x00FC
	.hword 0x01F8
	.hword 0x000F
	.hword 0x0008
	.hword 0x00F7
	.hword 0x01FE
	.hword 0x000F
	.hword 0x00FA
	.hword 0x0001
	.hword 0x000F
	.hword 0x00F8
	.hword 0x01FA
	.hword 0x000F
	.hword 0x00FF
	.hword 0x01F8
	.hword 0x000F
	.hword 0x0002
	.hword 0x01FB
	.hword 0x000F
	.hword 0x0001
	.hword 0x01FF
	.hword 0x000F
	.hword 0x00FE
	.hword 0x0002
	.hword 0x000F
	.hword 0x00FB
	.hword 0x01F7
	.hword 0x000F
	.hword 0x0008
	.hword 0x00FB
	.hword 0x0003
	.hword 0x000F
	.hword 0x0000
	.hword 0x0002
	.hword 0x000F
	.hword 0x00F7
	.hword 0x0000
	.hword 0x000F
	.hword 0x00F9
	.hword 0x01F7
	.hword 0x000F
	.hword 0x00FE
	.hword 0x01F6
	.hword 0x000F
	.hword 0x0002
	.hword 0x01F9
	.hword 0x000F
	.hword 0x0003
	.hword 0x01FE
	.hword 0x000F
	.hword 0x00F6
	.hword 0x01FB
	.hword 0x000F
	.hword 0x0008
	.hword 0x00FD
	.hword 0x0005
	.hword 0x000F
	.hword 0x0002
	.hword 0x0001
	.hword 0x000F
	.hword 0x00F7
	.hword 0x0003
	.hword 0x000F
	.hword 0x00F6
	.hword 0x01F8
	.hword 0x000F
	.hword 0x00FB
	.hword 0x01F5
	.hword 0x000F
	.hword 0x0000
	.hword 0x01F7
	.hword 0x000F
	.hword 0x0004
	.hword 0x01FB
	.hword 0x000F
	.hword 0x00F4
	.hword 0x01FE
	.hword 0x000F
	.hword 0x0008
	.hword 0x0000
	.hword 0x11F3
	.hword 0x000F
	.hword 0x0005
	.hword 0x11F8
	.hword 0x000F
	.hword 0x00F9
	.hword 0x11F3
	.hword 0x000F
	.hword 0x00F4
	.hword 0x11FF
	.hword 0x000F
	.hword 0x00F9
	.hword 0x1004
	.hword 0x000F
	.hword 0x0000
	.hword 0x1004
	.hword 0x000F
	.hword 0x0005
	.hword 0x11FF
	.hword 0x000F
	.hword 0x00F4
	.hword 0x11F8
	.hword 0x000F
	.hword 0x0008
	.hword 0x00FD
	.hword 0x11F2
	.hword 0x000F
	.hword 0x0005
	.hword 0x11F6
	.hword 0x000F
	.hword 0x00F6
	.hword 0x11F4
	.hword 0x000F
	.hword 0x00F4
	.hword 0x1002
	.hword 0x000F
	.hword 0x00FB
	.hword 0x1006
	.hword 0x000F
	.hword 0x0003
	.hword 0x1004
	.hword 0x000F
	.hword 0x0007
	.hword 0x11FD
	.hword 0x000F
	.hword 0x00F2
	.hword 0x11FA
	.hword 0x000F
	.hword 0x0008
	.hword 0x00FC
	.hword 0x11F1
	.hword 0x000F
	.hword 0x0005
	.hword 0x11F4
	.hword 0x000F
	.hword 0x00F4
	.hword 0x11F4
	.hword 0x000F
	.hword 0x00F5
	.hword 0x1004
	.hword 0x000F
	.hword 0x00FD
	.hword 0x1007
	.hword 0x000F
	.hword 0x0005
	.hword 0x1003
	.hword 0x000F
	.hword 0x0008
	.hword 0x11FB
	.hword 0x000F
	.hword 0x00F1
	.hword 0x11FC
	.hword 0x000F
	.hword 0x0008
	.hword 0x00F9
	.hword 0x11F0
	.hword 0x000F
	.hword 0x0003
	.hword 0x11F2
	.hword 0x000F
	.hword 0x00F2
	.hword 0x11F6
	.hword 0x000F
	.hword 0x00F7
	.hword 0x1006
	.hword 0x000F
	.hword 0x0000
	.hword 0x1007
	.hword 0x000F
	.hword 0x0007
	.hword 0x1001
	.hword 0x000F
	.hword 0x0008
	.hword 0x11F8
	.hword 0x000F
	.hword 0x00F1
	.hword 0x11FF
	.hword 0x000F
	.hword 0x0008
	.hword 0x00F6
	.hword 0x11F1
	.hword 0x000F
	.hword 0x0000
	.hword 0x11F0
	.hword 0x000F
	.hword 0x00F1
	.hword 0x11F9
	.hword 0x000F
	.hword 0x00FA
	.hword 0x1008
	.hword 0x000F
	.hword 0x0004
	.hword 0x1006
	.hword 0x000F
	.hword 0x0009
	.hword 0x11FE
	.hword 0x000F
	.hword 0x0007
	.hword 0x11F5
	.hword 0x000F
	.hword 0x00F2
	.hword 0x1003
	.hword 0x000F
	.hword 0x0008
	.hword 0x00F0
	.hword 0x01FE
	.hword 0x0010
	.hword 0x00F5
	.hword 0x0007
	.hword 0x0010
	.hword 0x00FE
	.hword 0x000A
	.hword 0x0010
	.hword 0x0006
	.hword 0x0005
	.hword 0x0010
	.hword 0x000A
	.hword 0x01FB
	.hword 0x0010
	.hword 0x0005
	.hword 0x01F3
	.hword 0x0010
	.hword 0x00FC
	.hword 0x01F0
	.hword 0x0010
	.hword 0x00F3
	.hword 0x01F4
	.hword 0x0010
	.hword 0x0008
	.hword 0x00F0
	.hword 0x0000
	.hword 0x0011
	.hword 0x00F7
	.hword 0x0008
	.hword 0x0011
	.hword 0x0000
	.hword 0x000A
	.hword 0x0011
	.hword 0x0008
	.hword 0x0003
	.hword 0x0011
	.hword 0x000A
	.hword 0x01F9
	.hword 0x0011
	.hword 0x0003
	.hword 0x01F2
	.hword 0x0011
	.hword 0x00FA
	.hword 0x01F0
	.hword 0x0011
	.hword 0x00F1
	.hword 0x01F6
	.hword 0x0011
	.hword 0x000A
	.hword 0x0000
	.hword 0x0003
	.hword 0x0001
	.hword 0x0003
	.hword 0x0002
	.hword 0x000A
	.hword 0x0003
	.hword 0x0003
	.hword 0x0002
	.hword 0x0003
	.hword 0x0001
	.hword 0x0000
	.hword 0x0000
	.hword 0x0000
	.hword 0xFFFF
	.hword 0x0002
	.hword 0x0004
	.hword 0x0002
	.hword 0x0005
	.hword 0x0002
	.hword 0x0006
	.hword 0x0002
	.hword 0x0007
	.hword 0x0002
	.hword 0x0008
	.hword 0x0002
	.hword 0x0009
	.hword 0x0002
	.hword 0x000A
	.hword 0x0002
	.hword 0x000B
	.hword 0x0002
	.hword 0x000C
	.hword 0x0002
	.hword 0x000D
	.hword 0x0002
	.hword 0x000E
	.hword 0x0000
	.hword 0x0001
	.hword 0x0000
	.hword 0xFFFF
	.size SpriteAnim_ManimStatGain, . - SpriteAnim_ManimStatGain
