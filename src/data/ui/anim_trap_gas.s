@ AP (object/sprite animation) definition(s); root + frame/anim offset tables
@ computed via assembler label arithmetic (see include/ap.h). Byte-identical.

	.data
	.globl	SpriteAnim_GasTrapVertical
	.align	1, 0
	.type	 SpriteAnim_GasTrapVertical,object
SpriteAnim_GasTrapVertical:
	.short	.LSpriteAnim_GasTrapVertical_ftab - SpriteAnim_GasTrapVertical
	.short	.LSpriteAnim_GasTrapVertical_atab - SpriteAnim_GasTrapVertical
.LSpriteAnim_GasTrapVertical_ftab:
	.short	.LSpriteAnim_GasTrapVertical_f0 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f1 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f2 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f3 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f4 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f5 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f6 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f7 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f8 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f9 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f10 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f11 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f12 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f13 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f14 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f15 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f16 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f17 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f18 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f19 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f20 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f21 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f22 - .LSpriteAnim_GasTrapVertical_ftab
	.short	.LSpriteAnim_GasTrapVertical_f23 - .LSpriteAnim_GasTrapVertical_ftab
.LSpriteAnim_GasTrapVertical_atab:
	.short	.LSpriteAnim_GasTrapVertical_a0 - .LSpriteAnim_GasTrapVertical_atab
	.short	.LSpriteAnim_GasTrapVertical_a1 - .LSpriteAnim_GasTrapVertical_atab
.LSpriteAnim_GasTrapVertical_f0:
	.short	0x1
	.short	0xF0
	.short	0x41F8
	.short	0x0
.LSpriteAnim_GasTrapVertical_f1:
	.short	0x1
	.short	0xF0
	.short	0x41F8
	.short	0x2
.LSpriteAnim_GasTrapVertical_f2:
	.short	0x1
	.short	0xF0
	.short	0x41F8
	.short	0x4
.LSpriteAnim_GasTrapVertical_f3:
	.short	0x2
	.short	0xE8
	.short	0x41F8
	.short	0x6
	.short	0xF8
	.short	0x41F8
	.short	0x8
.LSpriteAnim_GasTrapVertical_f4:
	.short	0x3
	.short	0xF8
	.short	0x51F8
	.short	0x8
	.short	0xE0
	.short	0x41F8
	.short	0xA
	.short	0xF0
	.short	0x41F8
	.short	0xC
.LSpriteAnim_GasTrapVertical_f5:
	.short	0x4
	.short	0xF8
	.short	0x41F8
	.short	0x8
	.short	0xD2
	.short	0x41F8
	.short	0xE
	.short	0xE2
	.short	0x41F8
	.short	0x10
	.short	0xF2
	.short	0x41F8
	.short	0x12
.LSpriteAnim_GasTrapVertical_f6:
	.short	0x4
	.short	0xCC
	.short	0x41F8
	.short	0x14
	.short	0xDC
	.short	0x41F8
	.short	0x16
	.short	0xEC
	.short	0x41F8
	.short	0x18
	.short	0xF8
	.short	0x41F8
	.short	0x8
.LSpriteAnim_GasTrapVertical_f7:
	.short	0x4
	.short	0xCB
	.short	0x51F8
	.short	0x14
	.short	0xDB
	.short	0x51F8
	.short	0x16
	.short	0xEB
	.short	0x51F8
	.short	0x18
	.short	0xF8
	.short	0x51F8
	.short	0x8
.LSpriteAnim_GasTrapVertical_f8:
	.short	0x4
	.short	0xCA
	.short	0x41F8
	.short	0x14
	.short	0xDA
	.short	0x41F8
	.short	0x16
	.short	0xEA
	.short	0x41F8
	.short	0x18
	.short	0xF8
	.short	0x41F8
	.short	0x8
.LSpriteAnim_GasTrapVertical_f9:
	.short	0x3
	.short	0xC9
	.short	0x41F8
	.short	0x1A
	.short	0xE9
	.short	0x61F8
	.short	0x1A
	.short	0xD9
	.short	0x51F8
	.short	0x1A
.LSpriteAnim_GasTrapVertical_f10:
	.short	0x3
	.short	0xCA
	.short	0x41F8
	.short	0x1C
	.short	0xD9
	.short	0x51F8
	.short	0x1C
	.short	0xEA
	.short	0x61F8
	.short	0x1C
.LSpriteAnim_GasTrapVertical_f11:
	.short	0x3
	.short	0xCA
	.short	0x41F8
	.short	0x1E
	.short	0xD9
	.short	0x51F8
	.short	0x1E
	.short	0xEA
	.short	0x61F8
	.short	0x1E
.LSpriteAnim_GasTrapVertical_f12:
	.short	0x1
	.short	0x0
	.short	0x61F8
	.short	0x0
.LSpriteAnim_GasTrapVertical_f13:
	.short	0x1
	.short	0x0
	.short	0x61F8
	.short	0x2
.LSpriteAnim_GasTrapVertical_f14:
	.short	0x1
	.short	0x0
	.short	0x61F8
	.short	0x4
.LSpriteAnim_GasTrapVertical_f15:
	.short	0x2
	.short	0x8
	.short	0x61F8
	.short	0x6
	.short	0xF8
	.short	0x61F8
	.short	0x8
.LSpriteAnim_GasTrapVertical_f16:
	.short	0x3
	.short	0xF8
	.short	0x71F8
	.short	0x8
	.short	0x10
	.short	0x61F8
	.short	0xA
	.short	0x0
	.short	0x61F8
	.short	0xC
.LSpriteAnim_GasTrapVertical_f17:
	.short	0x4
	.short	0xF8
	.short	0x61F8
	.short	0x8
	.short	0x1E
	.short	0x61F8
	.short	0xE
	.short	0xE
	.short	0x61F8
	.short	0x10
	.short	0xFE
	.short	0x61F8
	.short	0x12
.LSpriteAnim_GasTrapVertical_f18:
	.short	0x4
	.short	0x24
	.short	0x61F8
	.short	0x14
	.short	0x14
	.short	0x61F8
	.short	0x16
	.short	0x4
	.short	0x61F8
	.short	0x18
	.short	0xF8
	.short	0x61F8
	.short	0x8
.LSpriteAnim_GasTrapVertical_f19:
	.short	0x4
	.short	0x25
	.short	0x71F8
	.short	0x14
	.short	0x15
	.short	0x71F8
	.short	0x16
	.short	0x5
	.short	0x71F8
	.short	0x18
	.short	0xF8
	.short	0x71F8
	.short	0x8
.LSpriteAnim_GasTrapVertical_f20:
	.short	0x4
	.short	0x26
	.short	0x61F8
	.short	0x14
	.short	0x16
	.short	0x61F8
	.short	0x16
	.short	0x6
	.short	0x61F8
	.short	0x18
	.short	0xF8
	.short	0x61F8
	.short	0x8
.LSpriteAnim_GasTrapVertical_f21:
	.short	0x3
	.short	0x27
	.short	0x61F8
	.short	0x1A
	.short	0x7
	.short	0x41F8
	.short	0x1A
	.short	0x17
	.short	0x71F8
	.short	0x1A
.LSpriteAnim_GasTrapVertical_f22:
	.short	0x3
	.short	0x26
	.short	0x61F8
	.short	0x1C
	.short	0x17
	.short	0x71F8
	.short	0x1C
	.short	0x6
	.short	0x41F8
	.short	0x1C
.LSpriteAnim_GasTrapVertical_f23:
	.short	0x3
	.short	0x26
	.short	0x61F8
	.short	0x1E
	.short	0x17
	.short	0x71F8
	.short	0x1E
	.short	0x6
	.short	0x41F8
	.short	0x1E
.LSpriteAnim_GasTrapVertical_a0:
	.short	0x1
	.short	0x0
	.short	0x3
	.short	0x1
	.short	0x3
	.short	0x2
	.short	0x3
	.short	0x1
	.short	0x3
	.short	0x2
	.short	0x3
	.short	0x1
	.short	0x3
	.short	0x2
	.short	0x1
	.short	0x3
	.short	0x1
	.short	0x4
	.short	0x2
	.short	0x5
	.short	0x3
	.short	0x6
	.short	0x3
	.short	0x7
	.short	0x4
	.short	0x8
	.short	0x4
	.short	0x7
	.short	0x4
	.short	0x8
	.short	0x4
	.short	0x7
	.short	0x4
	.short	0x8
	.short	0x6
	.short	0x9
	.short	0x6
	.short	0xA
	.short	0x5
	.short	0xB
	.short	0x0
	.short	0x1
	.short	0x0
	.short	0xFFFF
.LSpriteAnim_GasTrapVertical_a1:
	.short	0x1
	.short	0xC
	.short	0x3
	.short	0xD
	.short	0x3
	.short	0xE
	.short	0x3
	.short	0xD
	.short	0x3
	.short	0xE
	.short	0x3
	.short	0xD
	.short	0x3
	.short	0xE
	.short	0x1
	.short	0xF
	.short	0x1
	.short	0x10
	.short	0x2
	.short	0x11
	.short	0x3
	.short	0x12
	.short	0x3
	.short	0x13
	.short	0x4
	.short	0x14
	.short	0x4
	.short	0x13
	.short	0x4
	.short	0x14
	.short	0x4
	.short	0x13
	.short	0x4
	.short	0x14
	.short	0x6
	.short	0x15
	.short	0x6
	.short	0x16
	.short	0x5
	.short	0x17
	.short	0x0
	.short	0x1
	.short	0x0
	.short	0xFFFF
	.size	 SpriteAnim_GasTrapVertical, . - SpriteAnim_GasTrapVertical
	.globl	SpriteAnim_GasTrapHorizontal
	.align	1, 0
	.type	 SpriteAnim_GasTrapHorizontal,object
SpriteAnim_GasTrapHorizontal:
	.short	.LSpriteAnim_GasTrapHorizontal_ftab - SpriteAnim_GasTrapHorizontal
	.short	.LSpriteAnim_GasTrapHorizontal_atab - SpriteAnim_GasTrapHorizontal
.LSpriteAnim_GasTrapHorizontal_ftab:
	.short	.LSpriteAnim_GasTrapHorizontal_f0 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f1 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f2 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f3 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f4 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f5 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f6 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f7 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f8 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f9 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f10 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f11 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f12 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f13 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f14 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f15 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f16 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f17 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f18 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f19 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f20 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f21 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f22 - .LSpriteAnim_GasTrapHorizontal_ftab
	.short	.LSpriteAnim_GasTrapHorizontal_f23 - .LSpriteAnim_GasTrapHorizontal_ftab
.LSpriteAnim_GasTrapHorizontal_atab:
	.short	.LSpriteAnim_GasTrapHorizontal_a0 - .LSpriteAnim_GasTrapHorizontal_atab
	.short	.LSpriteAnim_GasTrapHorizontal_a1 - .LSpriteAnim_GasTrapHorizontal_atab
.LSpriteAnim_GasTrapHorizontal_f0:
	.short	0x1
	.short	0xF8
	.short	0x4000
	.short	0x0
.LSpriteAnim_GasTrapHorizontal_f1:
	.short	0x1
	.short	0xF8
	.short	0x4000
	.short	0x2
.LSpriteAnim_GasTrapHorizontal_f2:
	.short	0x1
	.short	0xF8
	.short	0x4000
	.short	0x4
.LSpriteAnim_GasTrapHorizontal_f3:
	.short	0x2
	.short	0xF8
	.short	0x4000
	.short	0x6
	.short	0x80F8
	.short	0x10
	.short	0x8
.LSpriteAnim_GasTrapHorizontal_f4:
	.short	0x3
	.short	0x80F8
	.short	0x2000
	.short	0x6
	.short	0xF8
	.short	0x4008
	.short	0x9
	.short	0x80F8
	.short	0x18
	.short	0xB
.LSpriteAnim_GasTrapHorizontal_f5:
	.short	0x3
	.short	0x80F8
	.short	0x0
	.short	0x6
	.short	0x40F8
	.short	0x8006
	.short	0xC
	.short	0x80F8
	.short	0x26
	.short	0x10
.LSpriteAnim_GasTrapHorizontal_f6:
	.short	0x3
	.short	0x80F8
	.short	0x0
	.short	0x6
	.short	0x40F8
	.short	0x8004
	.short	0x11
	.short	0xF8
	.short	0x4024
	.short	0x15
.LSpriteAnim_GasTrapHorizontal_f7:
	.short	0x3
	.short	0x80F8
	.short	0x2000
	.short	0x6
	.short	0x40F8
	.short	0xA005
	.short	0x11
	.short	0xF8
	.short	0x6025
	.short	0x15
.LSpriteAnim_GasTrapHorizontal_f8:
	.short	0x3
	.short	0x80F8
	.short	0x0
	.short	0x6
	.short	0x40F8
	.short	0x8006
	.short	0x11
	.short	0xF8
	.short	0x4026
	.short	0x15
.LSpriteAnim_GasTrapHorizontal_f9:
	.short	0x3
	.short	0xF8
	.short	0x4027
	.short	0x17
	.short	0xF8
	.short	0x6017
	.short	0x17
	.short	0xF8
	.short	0x5007
	.short	0x17
.LSpriteAnim_GasTrapHorizontal_f10:
	.short	0x3
	.short	0xF8
	.short	0x5006
	.short	0x19
	.short	0xF8
	.short	0x6017
	.short	0x19
	.short	0xF8
	.short	0x4026
	.short	0x19
.LSpriteAnim_GasTrapHorizontal_f11:
	.short	0x3
	.short	0xF8
	.short	0x5006
	.short	0x1B
	.short	0xF8
	.short	0x6017
	.short	0x1B
	.short	0xF8
	.short	0x4026
	.short	0x1B
.LSpriteAnim_GasTrapHorizontal_f12:
	.short	0x1
	.short	0xF8
	.short	0x51F0
	.short	0x0
.LSpriteAnim_GasTrapHorizontal_f13:
	.short	0x1
	.short	0xF8
	.short	0x51F0
	.short	0x2
.LSpriteAnim_GasTrapHorizontal_f14:
	.short	0x1
	.short	0xF8
	.short	0x51F0
	.short	0x4
.LSpriteAnim_GasTrapHorizontal_f15:
	.short	0x2
	.short	0xF8
	.short	0x51F0
	.short	0x6
	.short	0x80F8
	.short	0x11E8
	.short	0x8
.LSpriteAnim_GasTrapHorizontal_f16:
	.short	0x3
	.short	0x80F8
	.short	0x31F8
	.short	0x6
	.short	0xF8
	.short	0x51E8
	.short	0x9
	.short	0x80F8
	.short	0x11E0
	.short	0xB
.LSpriteAnim_GasTrapHorizontal_f17:
	.short	0x3
	.short	0x80F8
	.short	0x11F8
	.short	0x6
	.short	0x40F8
	.short	0x91DA
	.short	0xC
	.short	0x80F8
	.short	0x11D2
	.short	0x10
.LSpriteAnim_GasTrapHorizontal_f18:
	.short	0x3
	.short	0x80F8
	.short	0x11F8
	.short	0x6
	.short	0x40F8
	.short	0x91DC
	.short	0x11
	.short	0xF8
	.short	0x51CC
	.short	0x15
.LSpriteAnim_GasTrapHorizontal_f19:
	.short	0x3
	.short	0x80F8
	.short	0x31F8
	.short	0x6
	.short	0x40F8
	.short	0xB1DB
	.short	0x11
	.short	0xF8
	.short	0x71CB
	.short	0x15
.LSpriteAnim_GasTrapHorizontal_f20:
	.short	0x3
	.short	0x80F8
	.short	0x11F8
	.short	0x6
	.short	0x40F8
	.short	0x91DA
	.short	0x11
	.short	0xF8
	.short	0x51CA
	.short	0x15
.LSpriteAnim_GasTrapHorizontal_f21:
	.short	0x3
	.short	0xF8
	.short	0x51C9
	.short	0x17
	.short	0xF8
	.short	0x71D9
	.short	0x17
	.short	0xF8
	.short	0x41E9
	.short	0x17
.LSpriteAnim_GasTrapHorizontal_f22:
	.short	0x3
	.short	0xF8
	.short	0x41EA
	.short	0x19
	.short	0xF8
	.short	0x71D9
	.short	0x19
	.short	0xF8
	.short	0x51CA
	.short	0x19
.LSpriteAnim_GasTrapHorizontal_f23:
	.short	0x3
	.short	0xF8
	.short	0x41EA
	.short	0x1B
	.short	0xF8
	.short	0x71D9
	.short	0x1B
	.short	0xF8
	.short	0x51CA
	.short	0x1B
.LSpriteAnim_GasTrapHorizontal_a0:
	.short	0x1
	.short	0x0
	.short	0x3
	.short	0x1
	.short	0x3
	.short	0x2
	.short	0x3
	.short	0x1
	.short	0x3
	.short	0x2
	.short	0x3
	.short	0x1
	.short	0x3
	.short	0x2
	.short	0x1
	.short	0x3
	.short	0x1
	.short	0x4
	.short	0x2
	.short	0x5
	.short	0x3
	.short	0x6
	.short	0x3
	.short	0x7
	.short	0x4
	.short	0x8
	.short	0x4
	.short	0x7
	.short	0x4
	.short	0x8
	.short	0x4
	.short	0x7
	.short	0x4
	.short	0x8
	.short	0x6
	.short	0x9
	.short	0x6
	.short	0xA
	.short	0x5
	.short	0xB
	.short	0x0
	.short	0x1
	.short	0x0
	.short	0xFFFF
.LSpriteAnim_GasTrapHorizontal_a1:
	.short	0x1
	.short	0xC
	.short	0x3
	.short	0xD
	.short	0x3
	.short	0xE
	.short	0x3
	.short	0xD
	.short	0x3
	.short	0xE
	.short	0x3
	.short	0xD
	.short	0x3
	.short	0xE
	.short	0x1
	.short	0xF
	.short	0x1
	.short	0x10
	.short	0x2
	.short	0x11
	.short	0x3
	.short	0x12
	.short	0x3
	.short	0x13
	.short	0x4
	.short	0x14
	.short	0x4
	.short	0x13
	.short	0x4
	.short	0x14
	.short	0x4
	.short	0x13
	.short	0x4
	.short	0x14
	.short	0x6
	.short	0x15
	.short	0x6
	.short	0x16
	.short	0x5
	.short	0x17
	.short	0x0
	.short	0x1
	.short	0x0
	.short	0xFFFF
	.size	 SpriteAnim_GasTrapHorizontal, . - SpriteAnim_GasTrapHorizontal
