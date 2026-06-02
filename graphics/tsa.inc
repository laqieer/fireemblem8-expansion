@ Tile-screen arrangement (TSA) row macro.
@
@ A TSA is an array of u16 GBA BG screen entries; one tsa_row holds a single
@ map row. Each u16 entry packs:
@   bits  0-9   tile index
@   bit   10    horizontal flip
@   bit   11    vertical flip
@   bits 12-15  palette bank
	.macro tsa_row entries:vararg
	.2byte \entries
	.endm
