@ AP (object/sprite animation) definition(s); root + frame/anim offset tables
@ computed via assembler label arithmetic (see include/ap.h). Byte-identical.

	.data
	.globl	SpriteAnim_BrownTextBox
	.align	1, 0
	.type	 SpriteAnim_BrownTextBox,object
SpriteAnim_BrownTextBox:
	.short	.LSpriteAnim_BrownTextBox_ftab - SpriteAnim_BrownTextBox
	.short	.LSpriteAnim_BrownTextBox_atab - SpriteAnim_BrownTextBox
.LSpriteAnim_BrownTextBox_ftab:
	.short	.LSpriteAnim_BrownTextBox_f0 - .LSpriteAnim_BrownTextBox_ftab
	.short	.LSpriteAnim_BrownTextBox_f1 - .LSpriteAnim_BrownTextBox_ftab
.LSpriteAnim_BrownTextBox_atab:
	.short	.LSpriteAnim_BrownTextBox_a0 - .LSpriteAnim_BrownTextBox_atab
	.short	.LSpriteAnim_BrownTextBox_a1 - .LSpriteAnim_BrownTextBox_atab
.LSpriteAnim_BrownTextBox_f0:
	.short	0x14
	.short	0x4000
	.short	0x4000
	.short	0x0
	.short	0x4000
	.short	0x4040
	.short	0x2
	.short	0x4000
	.short	0x20
	.short	0x2
	.short	0x4018
	.short	0x4000
	.short	0xD
	.short	0x4018
	.short	0x4040
	.short	0xF
	.short	0x4018
	.short	0x20
	.short	0xF
	.short	0x4008
	.short	0x4000
	.short	0x6
	.short	0x4008
	.short	0x4040
	.short	0x7
	.short	0x10
	.short	0x0
	.short	0xB
	.short	0x10
	.short	0x58
	.short	0xC
	.short	0x4008
	.short	0x20
	.short	0x7
	.short	0x4010
	.short	0x8
	.short	0x7
	.short	0x4010
	.short	0x18
	.short	0x7
	.short	0x4010
	.short	0x38
	.short	0x7
	.short	0x4010
	.short	0x48
	.short	0x7
	.short	0x4010
	.short	0x38
	.short	0x7
	.short	0x4010
	.short	0x28
	.short	0x7
	.short	0x4008
	.short	0x30
	.short	0x7
	.short	0x4000
	.short	0x30
	.short	0x2
	.short	0x4018
	.short	0x30
	.short	0xF
.LSpriteAnim_BrownTextBox_f1:
	.short	0xF
	.short	0x4000
	.short	0x4000
	.short	0x0
	.short	0x4000
	.short	0x4030
	.short	0x2
	.short	0x4000
	.short	0x20
	.short	0x2
	.short	0x4018
	.short	0x4000
	.short	0xD
	.short	0x4018
	.short	0x4030
	.short	0xF
	.short	0x4018
	.short	0x20
	.short	0xF
	.short	0x4008
	.short	0x4000
	.short	0x6
	.short	0x4008
	.short	0x4030
	.short	0x7
	.short	0x10
	.short	0x0
	.short	0xB
	.short	0x10
	.short	0x48
	.short	0xC
	.short	0x4008
	.short	0x20
	.short	0x7
	.short	0x4010
	.short	0x8
	.short	0x7
	.short	0x4010
	.short	0x18
	.short	0x7
	.short	0x4010
	.short	0x38
	.short	0x7
	.short	0x4010
	.short	0x28
	.short	0x7
.LSpriteAnim_BrownTextBox_a0:
	.short	0x4
	.short	0x0
	.short	0x0
	.short	0x0
	.short	0x0
	.short	0xFFFF
.LSpriteAnim_BrownTextBox_a1:
	.short	0x4
	.short	0x1
	.short	0x0
	.short	0x0
	.short	0x0
	.short	0xFFFF
	.short	0x0
	.size	 SpriteAnim_BrownTextBox, . - SpriteAnim_BrownTextBox
