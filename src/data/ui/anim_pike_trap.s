@ AP (object/sprite animation) definition(s); root + frame/anim offset tables
@ computed via assembler label arithmetic (see include/ap.h). Byte-identical.

	.data
	.globl	SpriteAnim_PikeTrap
	.align	1, 0
	.type	 SpriteAnim_PikeTrap,object
SpriteAnim_PikeTrap:
	.short	.LSpriteAnim_PikeTrap_ftab - SpriteAnim_PikeTrap
	.short	.LSpriteAnim_PikeTrap_atab - SpriteAnim_PikeTrap
.LSpriteAnim_PikeTrap_ftab:
	.short	.LSpriteAnim_PikeTrap_f0 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f1 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f2 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f3 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f4 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f5 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f6 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f7 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f8 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f9 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f10 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f11 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f12 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f13 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f14 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f15 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f16 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f17 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f18 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f19 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f20 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f21 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f22 - .LSpriteAnim_PikeTrap_ftab
	.short	.LSpriteAnim_PikeTrap_f23 - .LSpriteAnim_PikeTrap_ftab
.LSpriteAnim_PikeTrap_atab:
	.short	.LSpriteAnim_PikeTrap_a0 - .LSpriteAnim_PikeTrap_atab
	.short	.LSpriteAnim_PikeTrap_a1 - .LSpriteAnim_PikeTrap_atab
	.short	.LSpriteAnim_PikeTrap_a2 - .LSpriteAnim_PikeTrap_atab
.LSpriteAnim_PikeTrap_f0:
	.short	0x1
	.short	0x80F8
	.short	0x1F8
	.short	0x4
.LSpriteAnim_PikeTrap_f1:
	.short	0x3
	.short	0xF0
	.short	0x41EF
	.short	0x5
	.short	0x0
	.short	0x41EF
	.short	0x7
	.short	0xF8
	.short	0x41F8
	.short	0x3
.LSpriteAnim_PikeTrap_f2:
	.short	0x2
	.short	0xF8
	.short	0x41FC
	.short	0x3
	.short	0x80F8
	.short	0x1F8
	.short	0x2
.LSpriteAnim_PikeTrap_f3:
	.short	0x2
	.short	0xF8
	.short	0x41FA
	.short	0x0
	.short	0x80F8
	.short	0x1F8
	.short	0x2
.LSpriteAnim_PikeTrap_f4:
	.short	0x1
	.short	0xF8
	.short	0x41F8
	.short	0x0
.LSpriteAnim_PikeTrap_f5:
	.short	0x2
	.short	0xF8
	.short	0x41F9
	.short	0x0
	.short	0x80F8
	.short	0x1F8
	.short	0x2
.LSpriteAnim_PikeTrap_f6:
	.short	0x2
	.short	0x80F8
	.short	0x1FC
	.short	0x1
	.short	0x80F8
	.short	0x1F8
	.short	0x0
.LSpriteAnim_PikeTrap_f7:
	.short	0x1
	.short	0x80F8
	.short	0x1F8
	.short	0x1
.LSpriteAnim_PikeTrap_f8:
	.short	0x1
	.short	0x80F8
	.short	0x1000
	.short	0x4
.LSpriteAnim_PikeTrap_f9:
	.short	0x3
	.short	0xF0
	.short	0x5001
	.short	0x5
	.short	0x0
	.short	0x5001
	.short	0x7
	.short	0xF8
	.short	0x51F8
	.short	0x3
.LSpriteAnim_PikeTrap_f10:
	.short	0x2
	.short	0xF8
	.short	0x51F4
	.short	0x3
	.short	0x80F8
	.short	0x1000
	.short	0x2
.LSpriteAnim_PikeTrap_f11:
	.short	0x2
	.short	0xF8
	.short	0x51F6
	.short	0x0
	.short	0x80F8
	.short	0x1000
	.short	0x2
.LSpriteAnim_PikeTrap_f12:
	.short	0x1
	.short	0xF8
	.short	0x51F8
	.short	0x0
.LSpriteAnim_PikeTrap_f13:
	.short	0x2
	.short	0xF8
	.short	0x51F7
	.short	0x0
	.short	0x80F8
	.short	0x1000
	.short	0x2
.LSpriteAnim_PikeTrap_f14:
	.short	0x2
	.short	0x80F8
	.short	0x11FC
	.short	0x1
	.short	0x80F8
	.short	0x1000
	.short	0x0
.LSpriteAnim_PikeTrap_f15:
	.short	0x1
	.short	0x80F8
	.short	0x1000
	.short	0x1
.LSpriteAnim_PikeTrap_f16:
	.short	0x1
	.short	0x4000
	.short	0x1F8
	.short	0xB
.LSpriteAnim_PikeTrap_f17:
	.short	0x2
	.short	0x4000
	.short	0x81F0
	.short	0xF
	.short	0xF8
	.short	0x41F8
	.short	0xB
.LSpriteAnim_PikeTrap_f18:
	.short	0x2
	.short	0xF4
	.short	0x41F8
	.short	0xB
	.short	0x4000
	.short	0x1F8
	.short	0x2D
.LSpriteAnim_PikeTrap_f19:
	.short	0x2
	.short	0xF6
	.short	0x41F8
	.short	0x9
	.short	0x4000
	.short	0x1F8
	.short	0x2D
.LSpriteAnim_PikeTrap_f20:
	.short	0x1
	.short	0xF8
	.short	0x41F8
	.short	0x9
.LSpriteAnim_PikeTrap_f21:
	.short	0x2
	.short	0xF7
	.short	0x41F8
	.short	0x9
	.short	0x4000
	.short	0x1F8
	.short	0x2D
.LSpriteAnim_PikeTrap_f22:
	.short	0x2
	.short	0x40FC
	.short	0x1F8
	.short	0x9
	.short	0x4000
	.short	0x1F8
	.short	0xD
.LSpriteAnim_PikeTrap_f23:
	.short	0x1
	.short	0x4000
	.short	0x1F8
	.short	0x9
.LSpriteAnim_PikeTrap_a0:
	.short	0x3
	.short	0x0
	.short	0x2
	.short	0x1
	.short	0x2
	.short	0x2
	.short	0x2
	.short	0x3
	.short	0x2
	.short	0x4
	.short	0x2
	.short	0x3
	.short	0x2
	.short	0x4
	.short	0x2
	.short	0x3
	.short	0x2
	.short	0x4
	.short	0x2
	.short	0x5
	.short	0x32
	.short	0x4
	.short	0x2
	.short	0x6
	.short	0x2
	.short	0x7
	.short	0x0
	.short	0x1
	.short	0x0
	.short	0xFFFF
.LSpriteAnim_PikeTrap_a1:
	.short	0x3
	.short	0x8
	.short	0x2
	.short	0x9
	.short	0x2
	.short	0xA
	.short	0x2
	.short	0xB
	.short	0x2
	.short	0xC
	.short	0x2
	.short	0xB
	.short	0x2
	.short	0xC
	.short	0x2
	.short	0xB
	.short	0x2
	.short	0xC
	.short	0x2
	.short	0xD
	.short	0x32
	.short	0xC
	.short	0x2
	.short	0xE
	.short	0x2
	.short	0xF
	.short	0x0
	.short	0x1
	.short	0x0
	.short	0xFFFF
.LSpriteAnim_PikeTrap_a2:
	.short	0x3
	.short	0x10
	.short	0x2
	.short	0x11
	.short	0x2
	.short	0x12
	.short	0x2
	.short	0x13
	.short	0x2
	.short	0x14
	.short	0x2
	.short	0x13
	.short	0x2
	.short	0x14
	.short	0x2
	.short	0x13
	.short	0x2
	.short	0x14
	.short	0x2
	.short	0x15
	.short	0x32
	.short	0x14
	.short	0x2
	.short	0x16
	.short	0x2
	.short	0x17
	.short	0x0
	.short	0x1
	.short	0x0
	.short	0xFFFF
	.size	 SpriteAnim_PikeTrap, . - SpriteAnim_PikeTrap
