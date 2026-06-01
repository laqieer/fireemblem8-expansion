    .section .data

	.global gUnknown_08AA704C
gUnknown_08AA704C:  @ 0x08AA704C
        @ PROC_REPEAT
        .short 0x3, 0x0
        .word sub_80C8554
        @ PROC_END
        .short 0x0, 0x0
        .word 0x0

	.global gUnknown_08AA705C
gUnknown_08AA705C:  @ 0x08AA705C
        @ PROC_SET_END_CB
        .short 0x4, 0x0
        .word sub_80C85FC
        @ PROC_CALL
        .short 0x2, 0x0
        .word sub_80C8580
        @ PROC_REPEAT
        .short 0x3, 0x0
        .word sub_80C85B0
        @ PROC_END
        .short 0x0, 0x0
        .word 0x0


	.global gUnknown_08AA707C
gUnknown_08AA707C:  @ 0x08AA707C
        @ PROC_SET_END_CB
        .short 0x4, 0x0
        .word sub_80C8684
        @ PROC_CALL
        .short 0x2, 0x0
        .word sub_80C8608
        @ PROC_REPEAT
        .short 0x3, 0x0
        .word sub_80C8638
        @ PROC_END
        .short 0x0, 0x0
        .word 0x0


	.global gUnknown_08AA709C
gUnknown_08AA709C:  @ 0x08AA709C
	.incbin "graphics/misc/gUnknown_08AA709C.4bpp"

	.global gUnknown_08AA70BC
gUnknown_08AA70BC:  @ 0x08AA70BC
	.incbin "graphics/misc/gUnknown_08AA70BC.4bpp"

	.global gUnknown_08AA70DC
gUnknown_08AA70DC:  @ 0x08AA70DC
	@ 2 OAM objects (PutSpriteExt object list)
	.2byte 2
	.2byte 0x0000, 0xC000, 0x03C0  @ obj 0
	.2byte 0x8000, 0xC040, 0x03C0  @ obj 1
.L_end_gUnknown_08AA70DC:
	.if (.L_end_gUnknown_08AA70DC - gUnknown_08AA70DC) != 14
	.error "gUnknown_08AA70DC size mismatch"
	.endif

	.global gUnknown_08AA70EA
gUnknown_08AA70EA:  @ 0x08AA70EA
	@ 7 OAM objects (PutSpriteExt object list)
	.2byte 7
	.2byte 0x4000, 0xC000, 0x03C0  @ obj 0
	.2byte 0x0000, 0x8040, 0x03C0  @ obj 1
	.2byte 0x8000, 0x8060, 0x03C0  @ obj 2
	.2byte 0x4020, 0x8000, 0x03C0  @ obj 3
	.2byte 0x4020, 0x8020, 0x03C0  @ obj 4
	.2byte 0x4020, 0x8040, 0x03C0  @ obj 5
	.2byte 0x0020, 0x4060, 0x03C0  @ obj 6
.L_end_gUnknown_08AA70EA:
	.if (.L_end_gUnknown_08AA70EA - gUnknown_08AA70EA) != 44
	.error "gUnknown_08AA70EA size mismatch"
	.endif

	.global gUnknown_08AA7116
gUnknown_08AA7116:  @ 0x08AA7116
	@ 3 OAM objects (PutSpriteExt object list)
	.2byte 3
	.2byte 0x0000, 0xC000, 0x03C0  @ obj 0
	.2byte 0x4040, 0x8000, 0x03C0  @ obj 1
	.2byte 0x4040, 0x8020, 0x03C0  @ obj 2
.L_end_gUnknown_08AA7116:
	.if (.L_end_gUnknown_08AA7116 - gUnknown_08AA7116) != 20
	.error "gUnknown_08AA7116 size mismatch"
	.endif

	.global gUnknown_08AA712A
gUnknown_08AA712A:  @ 0x08AA712A
	@ 8 OAM objects (PutSpriteExt object list)
	.2byte 8
	.2byte 0x8000, 0xC000, 0x03C0  @ obj 0
	.2byte 0x8000, 0x8020, 0x03C0  @ obj 1
	.2byte 0x8020, 0x8020, 0x03C0  @ obj 2
	.2byte 0x8000, 0x4030, 0x03C0  @ obj 3
	.2byte 0x8020, 0x4030, 0x03C0  @ obj 4
	.2byte 0x4040, 0x8000, 0x03C0  @ obj 5
	.2byte 0x0040, 0x4020, 0x03C0  @ obj 6
	.2byte 0x8040, 0x0030, 0x03C0  @ obj 7
.L_end_gUnknown_08AA712A:
	.if (.L_end_gUnknown_08AA712A - gUnknown_08AA712A) != 50
	.error "gUnknown_08AA712A size mismatch"
	.endif

	.global gUnknown_08AA715C
gUnknown_08AA715C:  @ 0x08AA715C
	@ 9 OAM objects (PutSpriteExt object list)
	.2byte 9
	.2byte 0x4000, 0xC000, 0x03C0  @ obj 0
	.2byte 0x4000, 0xC040, 0x03C0  @ obj 1
	.2byte 0x4000, 0xC080, 0x03C0  @ obj 2
	.2byte 0x4020, 0x8000, 0x03C0  @ obj 3
	.2byte 0x4020, 0x8020, 0x03C0  @ obj 4
	.2byte 0x4020, 0x8040, 0x03C0  @ obj 5
	.2byte 0x4020, 0x8060, 0x03C0  @ obj 6
	.2byte 0x4020, 0x8080, 0x03C0  @ obj 7
	.2byte 0x4020, 0x80A0, 0x03C0  @ obj 8
.L_end_gUnknown_08AA715C:
	.if (.L_end_gUnknown_08AA715C - gUnknown_08AA715C) != 56
	.error "gUnknown_08AA715C size mismatch"
	.endif

	.global gUnknown_08AA7194
gUnknown_08AA7194:  @ 0x08AA7194
	@ 8 OAM objects (PutSpriteExt object list)
	.2byte 8
	.2byte 0x0000, 0xC000, 0x03C0  @ obj 0
	.2byte 0x8000, 0xC040, 0x03C0  @ obj 1
	.2byte 0x8000, 0x8060, 0x03C0  @ obj 2
	.2byte 0x8020, 0x8060, 0x03C0  @ obj 3
	.2byte 0x4040, 0x8000, 0x03C0  @ obj 4
	.2byte 0x4040, 0x8020, 0x03C0  @ obj 5
	.2byte 0x4040, 0x8040, 0x03C0  @ obj 6
	.2byte 0x0040, 0x4060, 0x03C0  @ obj 7
	.2byte 0x0000  @ pad
.L_end_gUnknown_08AA7194:
	.if (.L_end_gUnknown_08AA7194 - gUnknown_08AA7194) != 52
	.error "gUnknown_08AA7194 size mismatch"
	.endif
